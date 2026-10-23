
import os
import hmac
import secrets
import sqlite3
import logging
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path

import razorpay
from dotenv import load_dotenv
from flask import (
    Flask, abort, flash, redirect, render_template,
    request, session, url_for
)
from jinja2 import DictLoader
from werkzeug.security import generate_password_hash, check_password_hash

# =========================================================
# SMARTPAY COMMERCE - SINGLE FILE APPLICATION
# All HTML templates and routes are included in this file.
# Keep your existing .env and smartpay.db files.
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

DATABASE_PATH = BASE_DIR / "smartpay.db"

KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()

LEGACY_PRODUCT_NAME = "Wireless Headphones"
LEGACY_PRICE_PAISE = 49900

logging.basicConfig(level=logging.INFO)


# =========================================================
# DATABASE
# =========================================================

def get_connection():
    conn = sqlite3.connect(DATABASE_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def columns(conn, table):
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def initialize_database():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS merchant_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price_paise INTEGER NOT NULL DEFAULT 49900,
                image_emoji TEXT NOT NULL DEFAULT '📦',
                stock INTEGER NOT NULL DEFAULT 10,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        for name, definition in {
            "description": "TEXT NOT NULL DEFAULT ''",
            "price_paise": "INTEGER NOT NULL DEFAULT 49900",
            "image_emoji": "TEXT NOT NULL DEFAULT '📦'",
            "stock": "INTEGER NOT NULL DEFAULT 10",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "created_at": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in columns(conn, "products"):
                conn.execute(
                    f"ALTER TABLE products ADD COLUMN {name} {definition}"
                )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                address TEXT NOT NULL,
                total_paise INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                fulfillment_status TEXT NOT NULL DEFAULT 'new',
                payment_link_id TEXT UNIQUE,
                stock_deducted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        for name, definition in {
            "user_id": "INTEGER",
            "fulfillment_status": "TEXT NOT NULL DEFAULT 'new'",
            "payment_link_id": "TEXT",
            "stock_deducted": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in columns(conn, "orders"):
                conn.execute(
                    f"ALTER TABLE orders ADD COLUMN {name} {definition}"
                )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER,
                product_name TEXT NOT NULL,
                unit_price_paise INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                line_total_paise INTEGER NOT NULL,
                FOREIGN KEY(order_id) REFERENCES orders(id)
            )
        """)

        for name, definition in {
            "product_id": "INTEGER",
            "product_name": "TEXT NOT NULL DEFAULT 'Product'",
            "unit_price_paise": "INTEGER NOT NULL DEFAULT 0",
            "quantity": "INTEGER NOT NULL DEFAULT 1",
            "line_total_paise": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in columns(conn, "order_items"):
                conn.execute(
                    f"ALTER TABLE order_items ADD COLUMN {name} {definition}"
                )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_link_id TEXT UNIQUE NOT NULL,
                product_name TEXT NOT NULL,
                amount_paise INTEGER NOT NULL,
                status TEXT NOT NULL,
                order_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        if "order_id" not in columns(conn, "payments"):
            conn.execute("ALTER TABLE payments ADD COLUMN order_id INTEGER")

        count = conn.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]

        if count == 0:
            conn.executemany("""
                INSERT INTO products
                (name, description, price_paise, image_emoji, stock)
                VALUES (?, ?, ?, ?, ?)
            """, [
                ("Wireless Headphones",
                 "Comfortable wireless headphones for daily listening.",
                 49900, "🎧", 10),
                ("Power Bank",
                 "Portable power bank for charging on the go.",
                 79900, "🔋", 10),
                ("Smart Watch",
                 "A stylish smartwatch for everyday use.",
                 129900, "⌚", 10),
            ])


def get_products(include_hidden=False):
    sql = "SELECT * FROM products"
    if not include_hidden:
        sql += " WHERE is_active=1"
    sql += " ORDER BY id DESC"

    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def get_product(product_id, include_hidden=False):
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return None

    sql = "SELECT * FROM products WHERE id=?"
    if not include_hidden:
        sql += " AND is_active=1"

    with get_connection() as conn:
        row = conn.execute(sql, (product_id,)).fetchone()

    return dict(row) if row else None


def create_customer(name, email, password, phone):
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO users (name, email, password_hash, phone)
            VALUES (?, ?, ?, ?)
        """, (
            name, email.lower(), generate_password_hash(password), phone
        ))
        return cur.lastrowid


def customer_by_email(email):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM users WHERE email=? COLLATE NOCASE
        """, (email.strip(),)).fetchone()
    return dict(row) if row else None


def customer_by_id(customer_id):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT id,name,email,phone,address,created_at
            FROM users WHERE id=?
        """, (customer_id,)).fetchone()
    return dict(row) if row else None


def update_customer_details(customer_id, phone, address):
    with get_connection() as conn:
        conn.execute("""
            UPDATE users SET phone=?, address=? WHERE id=?
        """, (phone, address, customer_id))


def merchant_count():
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM merchant_accounts"
        ).fetchone()[0]


def customer_orders(customer_id):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM orders WHERE user_id=? ORDER BY id DESC
        """, (customer_id,)).fetchall()
    return [dict(r) for r in rows]


def order_by_id(order_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()
    return dict(row) if row else None


def order_by_payment_link(payment_link_id):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM orders WHERE payment_link_id=?
        """, (payment_link_id,)).fetchone()
    return dict(row) if row else None


def order_items(order_id):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM order_items WHERE order_id=? ORDER BY id
        """, (order_id,)).fetchall()
    return [dict(r) for r in rows]


def all_orders():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT o.*, COALESCE(u.email,'') AS customer_email
            FROM orders o LEFT JOIN users u ON u.id=o.user_id
            ORDER BY o.id DESC
        """).fetchall()
    return [dict(r) for r in rows]


def create_order(customer_id, name, phone, address, cart):
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            lines = []
            total = 0

            for raw_id, raw_qty in cart.items():
                try:
                    pid, qty = int(raw_id), int(raw_qty)
                except (TypeError, ValueError):
                    raise ValueError("Invalid product or quantity.")

                if qty < 1:
                    raise ValueError("Quantity must be at least one.")

                p = conn.execute("""
                    SELECT * FROM products WHERE id=? AND is_active=1
                """, (pid,)).fetchone()

                if not p:
                    raise ValueError("A product is no longer available.")
                if qty > p["stock"]:
                    raise ValueError(f"Not enough stock for {p['name']}.")

                unit = int(p["price_paise"])
                subtotal = unit * qty
                total += subtotal
                lines.append((
                    pid, p["name"], unit, qty, subtotal
                ))

            if not lines:
                raise ValueError("Your cart is empty.")

            cur = conn.execute("""
                INSERT INTO orders
                (user_id, customer_name, phone, address,
                 total_paise, status, fulfillment_status)
                VALUES (?, ?, ?, ?, ?, 'pending', 'new')
            """, (
                customer_id, name, phone, address, total
            ))
            oid = cur.lastrowid

            conn.executemany("""
                INSERT INTO order_items
                (order_id, product_id, product_name,
                 unit_price_paise, quantity, line_total_paise)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [(oid, *line) for line in lines])

            conn.commit()
            return oid, total
        except Exception:
            conn.rollback()
            raise


def link_order(order_id, payment_link_id):
    with get_connection() as conn:
        conn.execute("""
            UPDATE orders SET payment_link_id=? WHERE id=?
        """, (payment_link_id, order_id))


def mark_order_paid(order_id):
    # Deduct stock at most once, after Razorpay verification.
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            order = conn.execute(
                "SELECT * FROM orders WHERE id=?", (order_id,)
            ).fetchone()

            if not order:
                raise ValueError("Order not found.")

            if order["status"] == "paid":
                conn.commit()
                return

            items = conn.execute(
                "SELECT * FROM order_items WHERE order_id=?", (order_id,)
            ).fetchall()

            for item in items:
                p = conn.execute(
                    "SELECT stock FROM products WHERE id=?",
                    (item["product_id"],)
                ).fetchone()

                if not p or p["stock"] < item["quantity"]:
                    conn.execute("""
                        UPDATE orders SET status='paid',
                        fulfillment_status='stock_review'
                        WHERE id=?
                    """, (order_id,))
                    conn.commit()
                    return

            for item in items:
                conn.execute("""
                    UPDATE products SET stock=stock-? WHERE id=?
                """, (item["quantity"], item["product_id"]))

            conn.execute("""
                UPDATE orders SET status='paid', stock_deducted=1
                WHERE id=?
            """, (order_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def update_order_status(order_id, status):
    allowed = {"pending", "paid", "failed", "cancelled"}
    if status not in allowed:
        raise ValueError("Invalid payment status.")
    with get_connection() as conn:
        conn.execute(
            "UPDATE orders SET status=? WHERE id=?", (status, order_id)
        )


def update_fulfilment(order_id, status):
    allowed = {
        "new", "processing", "shipped",
        "delivered", "cancelled", "stock_review"
    }
    if status not in allowed:
        raise ValueError("Invalid order status.")
    with get_connection() as conn:
        conn.execute("""
            UPDATE orders SET fulfillment_status=? WHERE id=?
        """, (status, order_id))


def save_payment(link_id, product_name, amount, status, order_id=None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO payments
            (payment_link_id, product_name, amount_paise, status, order_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(payment_link_id) DO UPDATE SET
                status=excluded.status,
                amount_paise=excluded.amount_paise,
                order_id=COALESCE(excluded.order_id, payments.order_id)
        """, (link_id, product_name, amount, status, order_id))


def all_payments():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.*, o.customer_name
            FROM payments p LEFT JOIN orders o ON o.id=p.order_id
            ORDER BY p.id DESC
        """).fetchall()
    return [dict(r) for r in rows]


def dashboard_stats():
    with get_connection() as conn:
        products = conn.execute("""
            SELECT COUNT(*) FROM products WHERE is_active=1
        """).fetchone()[0]
        orders = conn.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]
        pending = conn.execute("""
            SELECT COUNT(*) FROM orders WHERE status='pending'
        """).fetchone()[0]
        paid = conn.execute("""
            SELECT COALESCE(SUM(amount_paise),0)
            FROM payments WHERE status='paid'
        """).fetchone()[0]
    return {
        "products": products, "orders": orders,
        "pending": pending, "paid": paid
    }


def save_product(name, desc, price, emoji, stock, product_id=None):
    with get_connection() as conn:
        if product_id:
            conn.execute("""
                UPDATE products SET name=?,description=?,price_paise=?,
                image_emoji=?,stock=?,is_active=1 WHERE id=?
            """, (name, desc, price, emoji, stock, product_id))
        else:
            conn.execute("""
                INSERT INTO products
                (name,description,price_paise,image_emoji,stock)
                VALUES (?,?,?,?,?)
            """, (name, desc, price, emoji, stock))


# =========================================================
# SECURITY: CSRF PROTECTION AND LOGIN REQUIREMENTS
# =========================================================

def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def customer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("customer_id"):
            flash("Please log in as a customer first.", "info")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def merchant_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("merchant_logged_in"):
            return redirect(url_for("merchant_login"))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def check_csrf():
    if request.method == "POST" and request.endpoint != "razorpay_webhook":
        expected = session.get("_csrf_token", "")
        supplied = request.form.get("_csrf_token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            abort(400, description="Form token expired. Refresh and retry.")


# =========================================================
# ALL HTML TEMPLATES ARE INSIDE THIS FILE
# =========================================================

TEMPLATES = {
"base.html": r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}SmartPay Commerce{% endblock %}</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#172033;font-family:Arial,sans-serif}a{color:inherit;text-decoration:none}
nav{background:white;border-bottom:1px solid #e3e8f0;position:sticky;top:0;z-index:3}.navin{max-width:1150px;margin:auto;padding:15px 20px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.brand{font-size:24px;font-weight:850}.brand span{color:#3157d5}.links{display:flex;align-items:center;gap:5px;flex-wrap:wrap}.links a,.links button{padding:9px 10px;border:0;border-radius:8px;background:transparent;color:#526079;font-size:13px;cursor:pointer}.links a:hover,.links button:hover{background:#eef2ff;color:#3157d5}.links form{display:inline}
.wrap{max-width:1150px;margin:auto;padding:28px 20px 50px;min-height:75vh}.hero{padding:38px;border:1px solid #dce5ff;border-radius:22px;background:linear-gradient(120deg,#edf2ff,white);display:flex;align-items:center;justify-content:space-between;gap:25px;flex-wrap:wrap}.hero h1{font-size:clamp(30px,4vw,45px);line-height:1.14}.hero-art{font-size:88px}
h1{letter-spacing:-.8px}h2{letter-spacing:-.3px}.muted{color:#667085;line-height:1.65}.eyebrow{color:#3157d5;text-transform:uppercase;font-weight:800;letter-spacing:1.2px;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));gap:18px}.card{background:white;border:1px solid #e2e7f0;border-radius:16px;padding:21px;box-shadow:0 5px 20px #17203308}.art{display:grid;place-items:center;min-height:150px;background:#edf1ff;border-radius:12px;font-size:70px;margin-bottom:16px}.price{font-size:26px;font-weight:850;margin:12px 0}
.btn{display:inline-block;border:1px solid transparent;border-radius:9px;padding:11px 15px;font-size:14px;font-weight:750;cursor:pointer;text-align:center}.primary{background:#3157d5;color:white}.secondary{background:white;border-color:#cbd5ff;color:#3157d5}.danger{background:#fff0ef;border-color:#ffd3ce;color:#a3261a}.full{width:100%}
.field{margin:14px 0}.field label{display:block;font-weight:700;font-size:14px;margin-bottom:7px}.field input,.field textarea,.field select{width:100%;border:1px solid #cbd3df;border-radius:8px;padding:12px;background:white;font:inherit}
.formcard{max-width:620px;margin:auto}.flash{padding:12px 15px;border-radius:9px;margin-bottom:12px;background:#eef2ff;color:#2944a0}.flash.error{background:#fff0ef;color:#a3261a}.flash.success{background:#e7f7ed;color:#116b3a}.badge{display:inline-block;padding:6px 9px;border-radius:18px;background:#fff2d5;color:#825900;font-size:11px;font-weight:800}.status{display:inline-block;background:#e7f7ed;color:#116b3a;border-radius:20px;padding:5px 9px;font-size:12px;font-weight:800}
.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;background:white;margin-top:12px}th,td{padding:12px;text-align:left;border-bottom:1px solid #e5e9f1;font-size:13px;vertical-align:top}th{background:#fafbfe;color:#667085}.empty{text-align:center;padding:38px 15px}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px}.statval{font-size:29px;font-weight:850}.sectionhead{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:29px 0 16px}.small{font-size:12px}.footer{border-top:1px solid #e3e8f0;background:white;text-align:center;padding:20px;color:#667085;font-size:12px}
@media(max-width:650px){.wrap{padding:18px 12px 35px}.navin{padding:12px}.links{width:100%}.links a,.links button{padding:7px;font-size:12px}.hero{padding:23px}.hero-art{font-size:65px}}
</style></head><body>
<nav><div class="navin"><a class="brand" href="{{ url_for('home') }}">Smart<span>Pay</span></a><div class="links">
<a href="{{ url_for('home') }}">Home</a><a href="{{ url_for('products') }}">Products</a><a href="{{ url_for('cart') }}">Cart ({{ (session.get('cart') or {})|length }})</a>
{% if session.get('customer_id') %}<a href="{{ url_for('my_orders') }}">My Orders</a><form method="post" action="{{ url_for('logout') }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><button>Logout</button></form>
{% else %}<a href="{{ url_for('login') }}">Customer Login</a><a href="{{ url_for('register') }}">Register</a>{% endif %}
{% if session.get('merchant_logged_in') %}<a href="{{ url_for('merchant_dashboard') }}">Merchant Dashboard</a><a href="{{ url_for('payment_history') }}">Payments</a><form method="post" action="{{ url_for('merchant_logout') }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><button>Merchant Logout</button></form>
{% else %}<a href="{{ url_for('merchant_login') }}">Merchant Portal</a>{% endif %}<span class="badge">TEST MODE</span>
</div></div></nav>
<main class="wrap">{% with msgs=get_flashed_messages(with_categories=true) %}{% for cat,msg in msgs %}<div class="flash {{ cat }}">{{ msg }}</div>{% endfor %}{% endwith %}{% block content %}{% endblock %}</main>
<footer class="footer">SmartPay Commerce · Flask · SQLite · Razorpay Test Mode</footer></body></html>''',

"index.html": r'''{% extends 'base.html' %}{% block title %}Home | SmartPay{% endblock %}{% block content %}
<section class="hero"><div><span class="eyebrow">Online store demo</span><h1>Smart shopping.<br><span style="color:#3157d5">Simple payments.</span></h1><p class="muted">Browse products, manage your cart and check out through Razorpay Test Mode.</p><a class="btn primary" href="{{ url_for('products') }}">Explore Products</a> <a class="btn secondary" href="{{ url_for('register') }}">Create Account</a></div><div class="hero-art">🛍️</div></section>
<div class="sectionhead"><h2>Featured products</h2><a class="btn secondary" href="{{ url_for('products') }}">View all</a></div>
<div class="grid">{% for p in products %}<article class="card"><a href="{{ url_for('product_detail',product_id=p.id) }}"><div class="art">{{ p.image_emoji }}</div><h2>{{ p.name }}</h2></a><p class="muted">{{ p.description }}</p><div class="price">₹{{ '%.2f'|format(p.price_paise/100) }}</div><form method="post" action="{{ url_for('add_to_cart',product_id=p.id) }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><button class="btn primary full">Add to Cart</button></form></article>{% else %}<p>No products yet.</p>{% endfor %}</div>
<div class="sectionhead"><h2>Choose your portal</h2></div><div class="grid"><div class="card"><h2>🛒 Customer</h2><p class="muted">Register, shop, checkout and track orders.</p><a class="btn secondary" href="{{ url_for('products') }}">Start shopping</a></div><div class="card"><h2>📊 Merchant</h2><p class="muted">Manage products, inventory, orders and payment records.</p><a class="btn secondary" href="{{ url_for('merchant_login') }}">Open Merchant Portal</a></div></div>
{% endblock %}''',

"products.html": r'''{% extends 'base.html' %}{% block title %}Products | SmartPay{% endblock %}{% block content %}
<span class="eyebrow">Store catalogue</span><h1>Products</h1><p class="muted">Browse products available in our demo store.</p>
<form method="get" action="{{ url_for('products') }}" style="display:flex;gap:8px;max-width:550px"><input name="q" value="{{ query }}" placeholder="Search products" style="flex:1;min-width:100px;padding:12px;border:1px solid #cbd3df;border-radius:8px"><button class="btn primary">Search</button></form>
<div class="grid" style="margin-top:22px">{% for p in products %}<article class="card"><a href="{{ url_for('product_detail',product_id=p.id) }}"><div class="art">{{ p.image_emoji }}</div><h2>{{ p.name }}</h2></a><p class="muted">{{ p.description }}</p><div class="price">₹{{ '%.2f'|format(p.price_paise/100) }}</div><p class="muted small">Stock: {{ p.stock }}</p>{% if p.stock>0 %}<form method="post" action="{{ url_for('add_to_cart',product_id=p.id) }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><button class="btn primary full">Add to Cart</button></form>{% else %}<span class="badge">Out of stock</span>{% endif %}</article>{% else %}<div class="card empty">No matching products found.</div>{% endfor %}</div>{% endblock %}''',

"product_detail.html": r'''{% extends 'base.html' %}{% block title %}{{ product.name }} | SmartPay{% endblock %}{% block content %}<div class="card"><div class="grid"><div class="art" style="font-size:100px">{{ product.image_emoji }}</div><div><span class="eyebrow">Product details</span><h1>{{ product.name }}</h1><p class="muted">{{ product.description }}</p><div class="price">₹{{ '%.2f'|format(product.price_paise/100) }}</div><p class="muted">Available stock: {{ product.stock }}</p><form method="post" action="{{ url_for('add_to_cart',product_id=product.id) }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><button class="btn primary">Add to Cart</button></form><p><a class="btn secondary" href="{{ url_for('products') }}">Back to Products</a></p></div></div></div>{% endblock %}''',

"register.html": r'''{% extends 'base.html' %}{% block title %}Register | SmartPay{% endblock %}{% block content %}<div class="card formcard"><span class="eyebrow">Customer portal</span><h1>Create account</h1>{% if error %}<div class="flash error">{{ error }}</div>{% endif %}<form method="post"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><div class="field"><label>Full name</label><input name="name" required maxlength="100"></div><div class="field"><label>Email</label><input name="email" type="email" required maxlength="254"></div><div class="field"><label>Mobile (optional)</label><input name="phone" maxlength="20"></div><div class="field"><label>Password (minimum 8 characters)</label><input name="password" type="password" minlength="8" required></div><button class="btn primary full">Create Account</button></form><p class="muted">Already registered? <a href="{{ url_for('login') }}">Login</a></p></div>{% endblock %}''',

"login.html": r'''{% extends 'base.html' %}{% block title %}Customer Login | SmartPay{% endblock %}{% block content %}<div class="card formcard"><span class="eyebrow">Customer portal</span><h1>Customer Login</h1>{% if error %}<div class="flash error">{{ error }}</div>{% endif %}<form method="post"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><div class="field"><label>Email</label><input name="email" type="email" required></div><div class="field"><label>Password</label><input name="password" type="password" required></div><button class="btn primary full">Login</button></form><p class="muted">New customer? <a href="{{ url_for('register') }}">Create account</a></p></div>{% endblock %}''',

"cart.html": r'''{% extends 'base.html' %}{% block title %}Cart | SmartPay{% endblock %}{% block content %}<span class="eyebrow">Your shopping bag</span><h1>Shopping Cart</h1>{% if items %}<div class="card tablewrap"><table><thead><tr><th>Product</th><th>Unit price</th><th>Quantity</th><th>Total</th><th>Update/remove</th></tr></thead><tbody>{% for p in items %}<tr><td>{{ p.image_emoji }} <strong>{{ p.name }}</strong></td><td>₹{{ '%.2f'|format(p.price_paise/100) }}</td><td>{{ p.quantity }}</td><td>₹{{ '%.2f'|format(p.line_total_paise/100) }}</td><td><form method="post" action="{{ url_for('update_cart_item',product_id=p.id) }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><input type="number" name="quantity" min="0" max="{{ p.stock }}" value="{{ p.quantity }}" style="width:65px;padding:8px;border:1px solid #ccd3df;border-radius:7px"><button class="btn secondary">Set</button></form><form method="post" action="{{ url_for('remove_from_cart',product_id=p.id) }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><button class="btn danger" style="margin-top:5px">Remove</button></form></td></tr>{% endfor %}</tbody></table><div class="price">Total: ₹{{ '%.2f'|format(total/100) }}</div><a class="btn secondary" href="{{ url_for('products') }}">Continue shopping</a> <a class="btn primary" href="{{ url_for('checkout') }}">Proceed to Checkout</a></div>{% else %}<div class="card empty"><div style="font-size:50px">🛒</div><h2>Cart is empty</h2><a class="btn primary" href="{{ url_for('products') }}">Browse Products</a></div>{% endif %}{% endblock %}''',

"checkout.html": r'''{% extends 'base.html' %}{% block title %}Checkout | SmartPay{% endblock %}{% block content %}<span class="eyebrow">Checkout</span><h1>Delivery Details</h1>{% if error %}<div class="flash error">{{ error }}</div>{% endif %}<div class="grid"><section class="card"><form method="post"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><div class="field"><label>Name</label><input value="{{ user.name }}" readonly></div><div class="field"><label>Mobile</label><input name="phone" value="{{ user.phone }}" required maxlength="20"></div><div class="field"><label>Delivery address</label><textarea name="address" rows="5" maxlength="500" required>{{ user.address }}</textarea></div><button class="btn primary full">Place Order & Continue to Payment</button></form><p class="muted small">Razorpay Test Mode only.</p></section><section class="card"><h2>Order Summary</h2>{% for p in items %}<p>{{ p.name }} × {{ p.quantity }} <strong style="float:right">₹{{ '%.2f'|format(p.line_total_paise/100) }}</strong></p>{% endfor %}<hr><div class="price">₹{{ '%.2f'|format(total/100) }}</div></section></div>{% endblock %}''',

"order_result.html": r'''{% extends 'base.html' %}{% block title %}Payment Result | SmartPay{% endblock %}{% block content %}<div class="card empty">{% if status=='paid' %}<div style="font-size:55px">✅</div><span class="status">VERIFIED PAID</span><h1>Payment Successful</h1>{% elif status=='pending' %}<h1>Payment Awaiting Confirmation</h1>{% else %}<h1>Payment Result Unavailable</h1>{% endif %}<p class="muted">{{ message }}</p>{% if order %}<h2>Order #{{ order.id }}</h2><p>Total: ₹{{ '%.2f'|format(order.total_paise/100) }}</p><p>Payment status: {{ order.status|upper }}</p><p>Order status: {{ order.fulfillment_status|replace('_',' ')|title }}</p>{% endif %}<a class="btn primary" href="{{ url_for('my_orders') }}">My Orders</a> <a class="btn secondary" href="{{ url_for('products') }}">Continue shopping</a></div>{% endblock %}''',

"customer_orders.html": r'''{% extends 'base.html' %}{% block title %}My Orders | SmartPay{% endblock %}{% block content %}<span class="eyebrow">Customer portal</span><h1>My Orders</h1>{% if orders %}<div class="card tablewrap"><table><tr><th>Order</th><th>Date</th><th>Total</th><th>Payment</th><th>Fulfilment</th><th></th></tr>{% for o in orders %}<tr><td>#{{ o.id }}</td><td>{{ o.created_at }}</td><td>₹{{ '%.2f'|format(o.total_paise/100) }}</td><td>{{ o.status|upper }}</td><td>{{ o.fulfillment_status|title }}</td><td><a class="btn secondary" href="{{ url_for('customer_order_detail',order_id=o.id) }}">Details</a></td></tr>{% endfor %}</table></div>{% else %}<div class="card empty"><h2>No orders yet</h2><a class="btn primary" href="{{ url_for('products') }}">Shop products</a></div>{% endif %}{% endblock %}''',

"customer_order_detail.html": r'''{% extends 'base.html' %}{% block title %}Order Details | SmartPay{% endblock %}{% block content %}<h1>Order #{{ order.id }}</h1><div class="card">{% for i in items %}<p>{{ i.product_name }} × {{ i.quantity }} <strong style="float:right">₹{{ '%.2f'|format(i.line_total_paise/100) }}</strong></p>{% endfor %}<hr><h2>Total ₹{{ '%.2f'|format(order.total_paise/100) }}</h2><p>Payment: {{ order.status|upper }}</p><p>Fulfilment: {{ order.fulfillment_status|title }}</p><p>Address: {{ order.address }}</p></div>{% endblock %}''',

"merchant_login.html": r'''{% extends 'base.html' %}{% block title %}Merchant Login | SmartPay{% endblock %}{% block content %}<div class="card formcard"><span class="eyebrow">Restricted merchant portal</span><h1>Merchant Login</h1>{% if error %}<div class="flash error">{{ error }}</div>{% endif %}<form method="post"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><div class="field"><label>Username</label><input name="username" required></div><div class="field"><label>Password</label><input name="password" type="password" required></div><button class="btn primary full">Login</button></form>{% if registration_open %}<p class="muted">First time? <a href="{{ url_for('merchant_register') }}"><strong>Create Merchant Account</strong></a></p>{% else %}<p class="muted small">Merchant registration is closed because an account already exists.</p>{% endif %}</div>{% endblock %}''',

"merchant_register.html": r'''{% extends 'base.html' %}{% block title %}Create Merchant Account | SmartPay{% endblock %}{% block content %}<div class="card formcard"><span class="eyebrow">Initial merchant setup</span><h1>Create Merchant Account</h1><p class="muted">Only the first merchant account can be created from this page. Registration closes after setup.</p>{% if error %}<div class="flash error">{{ error }}</div>{% endif %}<form method="post"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><div class="field"><label>Choose username</label><input name="username" minlength="3" maxlength="40" required></div><div class="field"><label>Choose password (at least 10 characters)</label><input name="password" type="password" minlength="10" required></div><div class="field"><label>Confirm password</label><input name="confirm_password" type="password" minlength="10" required></div><button class="btn primary full">Create Merchant Account</button></form><p><a href="{{ url_for('merchant_login') }}">Back to Login</a></p></div>{% endblock %}''',

"merchant_dashboard.html": r'''{% extends 'base.html' %}{% block title %}Merchant Dashboard | SmartPay{% endblock %}{% block content %}<span class="eyebrow">Merchant workspace</span><h1>Dashboard</h1><div class="stats"><div class="card"><span class="muted">Active products</span><div class="statval">{{ stats.products }}</div></div><div class="card"><span class="muted">Orders</span><div class="statval">{{ stats.orders }}</div></div><div class="card"><span class="muted">Pending payments</span><div class="statval">{{ stats.pending }}</div></div><div class="card"><span class="muted">Verified payments</span><div class="statval">₹{{ '%.2f'|format(stats.paid/100) }}</div></div></div><div class="sectionhead"><h2>Manage Store</h2></div><div class="grid"><div class="card"><h2>📦 Products & Stock</h2><p class="muted">Add and edit products.</p><a class="btn primary" href="{{ url_for('merchant_products') }}">Manage Products</a></div><div class="card"><h2>🧾 Orders</h2><p class="muted">Review purchases and fulfilment.</p><a class="btn primary" href="{{ url_for('merchant_orders') }}">Manage Orders</a></div><div class="card"><h2>💳 Payments</h2><p class="muted">Verified payment records.</p><a class="btn primary" href="{{ url_for('payment_history') }}">Payment History</a></div></div>{% endblock %}''',

"merchant_products.html": r'''{% extends 'base.html' %}{% block title %}Manage Products | SmartPay{% endblock %}{% block content %}<div class="sectionhead"><div><span class="eyebrow">Merchant</span><h1>Products & Stock</h1></div><a class="btn primary" href="{{ url_for('merchant_product_new') }}">+ Add Product</a></div><div class="card tablewrap"><table><tr><th>Product</th><th>Price</th><th>Stock</th><th>Visible</th><th>Action</th></tr>{% for p in products %}<tr><td>{{ p.image_emoji }} <strong>{{ p.name }}</strong><div class="muted small">{{ p.description }}</div></td><td>₹{{ '%.2f'|format(p.price_paise/100) }}</td><td>{{ p.stock }}</td><td>{{ 'Yes' if p.is_active else 'Hidden' }}</td><td><a class="btn secondary" href="{{ url_for('merchant_product_edit',product_id=p.id) }}">Edit</a>{% if p.is_active %}<form method="post" action="{{ url_for('merchant_product_hide',product_id=p.id) }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><button class="btn danger">Hide</button></form>{% endif %}</td></tr>{% endfor %}</table></div>{% endblock %}''',

"product_form.html": r'''{% extends 'base.html' %}{% block title %}Product | SmartPay{% endblock %}{% block content %}<div class="card formcard"><h1>{{ 'Edit' if product else 'Add' }} Product</h1>{% if error %}<div class="flash error">{{ error }}</div>{% endif %}<form method="post"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><div class="field"><label>Name</label><input name="name" required maxlength="120" value="{{ product.name if product else '' }}"></div><div class="field"><label>Description</label><textarea name="description" required maxlength="1000">{{ product.description if product else '' }}</textarea></div><div class="field"><label>Price (₹)</label><input name="price" type="number" min="0.01" step="0.01" required value="{{ '%.2f'|format(product.price_paise/100) if product else '' }}"></div><div class="field"><label>Stock</label><input name="stock" type="number" min="0" max="1000000" required value="{{ product.stock if product else 10 }}"></div><div class="field"><label>Icon / emoji</label><input name="image_emoji" maxlength="8" value="{{ product.image_emoji if product else '📦' }}"></div><button class="btn primary full">Save Product</button></form></div>{% endblock %}''',

"merchant_orders.html": r'''{% extends 'base.html' %}{% block title %}Manage Orders | SmartPay{% endblock %}{% block content %}<span class="eyebrow">Merchant</span><h1>Customer Orders</h1><p class="muted">Payment status is separate from fulfilment status.</p><div class="card tablewrap"><table><tr><th>Order</th><th>Customer</th><th>Total</th><th>Payment</th><th>Fulfilment update</th></tr>{% for o in orders %}<tr><td>#{{ o.id }}<div class="muted small">{{ o.created_at }}</div></td><td>{{ o.customer_name }}<div class="muted small">{{ o.customer_email }}</div><div class="muted small">{{ o.phone }}</div><div class="muted small">{{ o.address }}</div></td><td>₹{{ '%.2f'|format(o.total_paise/100) }}</td><td>{{ o.status|upper }}</td><td><form method="post" action="{{ url_for('merchant_order_status',order_id=o.id) }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><select name="status"><option value="new">New</option><option value="processing">Processing</option><option value="shipped">Shipped</option><option value="delivered">Delivered</option><option value="cancelled">Cancelled</option><option value="stock_review">Stock review</option></select><button class="btn secondary">Save</button></form></td></tr>{% else %}<tr><td colspan="5">No orders yet.</td></tr>{% endfor %}</table></div>{% endblock %}''',

"payment_history.html": r'''{% extends 'base.html' %}{% block title %}Payment History | SmartPay{% endblock %}{% block content %}<span class="eyebrow">Merchant</span><h1>Payment History</h1><p class="muted">Records verified against Razorpay.</p><div class="card tablewrap"><table><tr><th>ID</th><th>Product / Order</th><th>Amount</th><th>Status</th><th>Order</th><th>Payment Link</th><th>Date</th></tr>{% for p in payments %}<tr><td>{{ p.id }}</td><td>{{ p.product_name }}{% if p.customer_name %}<div class="muted small">{{ p.customer_name }}</div>{% endif %}</td><td>₹{{ '%.2f'|format(p.amount_paise/100) }}</td><td>{{ p.status|upper }}</td><td>{{ p.order_id or 'Legacy payment' }}</td><td class="small">{{ p.payment_link_id }}</td><td>{{ p.created_at }}</td></tr>{% else %}<tr><td colspan="7">No payments recorded.</td></tr>{% endfor %}</table></div>{% endblock %}''',
}

app.jinja_loader = DictLoader(TEMPLATES)
app.jinja_env.globals["csrf_token"] = lambda: session.setdefault("_csrf_token", secrets.token_urlsafe(32))


# =========================================================
# RAZORPAY HELPERS
# =========================================================

def razorpay_client():
    if not KEY_ID or not KEY_SECRET:
        raise RuntimeError("Razorpay API keys are missing from .env")
    return razorpay.Client(auth=(KEY_ID, KEY_SECRET))


def verify_payment(link_id):
    link = razorpay_client().payment_link.fetch(link_id)
    link_status = str(link.get("status", "unknown")).lower()
    paid_amount = int(link.get("amount_paid", 0))
    link_amount = int(link.get("amount", 0))

    order = order_by_payment_link(link_id)

    if order:
        expected_amount = int(order["total_paise"])
        oid = order["id"]
        description = f"SmartPay Order #{oid}"
    else:
        expected_amount = LEGACY_PRICE_PAISE
        oid = None
        description = LEGACY_PRODUCT_NAME

    paid = (
        link_status == "paid"
        and link_amount == expected_amount
        and paid_amount >= expected_amount
    )

    if paid:
        if oid is not None:
            mark_order_paid(oid)
        save_payment(link_id, description, paid_amount, "paid", oid)

    return {
        "payment_link_id": link_id,
        "status": link_status,
        "amount_paid_paise": paid_amount,
        "verified_as_paid": paid,
        "saved_to_history": paid,
        "order_id": oid,
    }


# =========================================================
# HOME / PRODUCTS
# =========================================================

@app.route("/")
def home():
    return render_template("index.html", products=get_products()[:3])


@app.route("/products")
def products():
    query = request.args.get("q", "").strip().casefold()
    items = get_products()

    if query:
        items = [
            p for p in items
            if query in p["name"].casefold()
            or query in p["description"].casefold()
        ]

    return render_template("products.html", products=items, query=request.args.get("q", ""))


@app.route("/products/<int:product_id>")
def product_detail(product_id):
    p = get_product(product_id)
    if not p:
        abort(404)
    return render_template("product_detail.html", product=p)


# =========================================================
# CUSTOMER ACCOUNTS
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        if len(name) < 2 or "@" not in email or len(password) < 8:
            flash("Enter a valid name/email and password of at least 8 characters.", "error")
            return render_template("register.html"), 400

        try:
            cid = create_customer(name, email, password, phone)
        except sqlite3.IntegrityError:
            flash("That email is already registered. Please log in.", "error")
            return render_template("register.html"), 400

        cart = session.get("cart", {})
        session.clear()
        session["cart"] = cart
        session["_csrf_token"] = secrets.token_urlsafe(32)
        session["customer_id"] = cid
        session["customer_name"] = name

        flash("Account created successfully.", "success")
        return redirect(url_for("home"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = customer_by_email(email)

        if user and check_password_hash(user["password_hash"], password):
            cart = session.get("cart", {})
            session.clear()
            session["cart"] = cart
            session["_csrf_token"] = secrets.token_urlsafe(32)
            session["customer_id"] = user["id"]
            session["customer_name"] = user["name"]
            return redirect(url_for("home"))

        error = "Email or password is incorrect."

    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("home"))


# =========================================================
# CART
# =========================================================

def make_cart():
    items = []
    total = 0

    for raw_id, raw_qty in session.get("cart", {}).items():
        p = get_product(raw_id)
        if not p:
            continue

        try:
            qty = int(raw_qty)
        except (TypeError, ValueError):
            continue

        qty = min(qty, p["stock"])
        if qty < 1:
            continue

        item = dict(p)
        item["quantity"] = qty
        item["line_total_paise"] = qty * p["price_paise"]
        items.append(item)
        total += item["line_total_paise"]

    return items, total


@app.route("/cart")
def cart():
    items, total = make_cart()
    return render_template("cart.html", items=items, total=total)


@app.route("/cart/add/<int:product_id>", methods=["POST"])
def add_to_cart(product_id):
    p = get_product(product_id)
    if not p or p["stock"] < 1:
        flash("Product unavailable.", "error")
        return redirect(url_for("products"))

    cart = session.get("cart", {})
    key = str(product_id)

    try:
        qty = int(cart.get(key, 0))
    except (ValueError, TypeError):
        qty = 0

    if qty < p["stock"]:
        cart[key] = qty + 1
        session["cart"] = cart
        flash("Added to cart.", "success")
    else:
        flash("Maximum available stock is already in your cart.", "error")

    return redirect(url_for("cart"))


@app.route("/cart/update/<int:product_id>", methods=["POST"])
def update_cart_item(product_id):
    p = get_product(product_id)
    cart = session.get("cart", {})

    try:
        qty = int(request.form.get("quantity", "0"))
    except (TypeError, ValueError):
        qty = 0

    if qty <= 0 or not p:
        cart.pop(str(product_id), None)
    else:
        cart[str(product_id)] = min(qty, p["stock"])

    session["cart"] = cart
    return redirect(url_for("cart"))


@app.route("/cart/remove/<int:product_id>", methods=["POST"])
def remove_from_cart(product_id):
    cart = session.get("cart", {})
    cart.pop(str(product_id), None)
    session["cart"] = cart
    return redirect(url_for("cart"))


# =========================================================
# CHECKOUT / PAYMENT / ORDER HISTORY
# =========================================================

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("customer_id"):
            flash("Please log in before checkout.", "info")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    items, total = make_cart()
    user = customer_by_id(session["customer_id"])

    if not items:
        flash("Your cart is empty.", "info")
        return redirect(url_for("products"))

    error = None

    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        digits = "".join(c for c in phone if c.isdigit())

        if not 10 <= len(digits) <= 15 or not 8 <= len(address) <= 500:
            error = "Enter a valid phone number and delivery address."
        else:
            update_customer_details(user["id"], phone, address)
            oid = None

            try:
                oid, total_paise = create_order(
                    user["id"], user["name"], phone, address,
                    session.get("cart", {})
                )

                link = razorpay_client().payment_link.create({
                    "amount": total_paise,
                    "currency": "INR",
                    "accept_partial": False,
                    "description": f"SmartPay Order #{oid}",
                    "reference_id": f"SPORDER{oid}-{secrets.token_hex(4)}",
                    "callback_url": url_for("payment_return", _external=True),
                    "callback_method": "get",
                    "reminder_enable": False,
                    "notes": {
                        "order_id": str(oid),
                        "environment": "test",
                    },
                })

                if not link.get("id") or not link.get("short_url"):
                    raise RuntimeError("Razorpay did not return a valid link.")

                link_order(oid, link["id"])
                session.pop("cart", None)
                return redirect(link["short_url"], code=303)

            except ValueError as exc:
                error = str(exc)
            except Exception:
                app.logger.exception("Checkout/payment link creation failed")
                if oid is not None:
                    update_order_status(oid, "cancelled")
                error = "Could not create payment link. Check Test Mode keys and terminal."

    return render_template(
        "checkout.html", items=items, total=total,
        user=user, error=error
    )


@app.route("/payment-return")
def payment_return():
    link_id = request.args.get("razorpay_payment_link_id", "")

    if not link_id.startswith("plink_"):
        return render_template(
            "order_result.html", status="unknown", order=None,
            message="Payment could not be identified. Check your orders."
        )

    try:
        result = verify_payment(link_id)
        order = order_by_payment_link(link_id)

        if order and order.get("user_id") != session.get("customer_id"):
            order = None

        status = "paid" if result["verified_as_paid"] else "pending"
        message = (
            "Payment confirmed by Razorpay."
            if status == "paid"
            else "Payment is not confirmed yet. Check My Orders before retrying."
        )

        return render_template(
            "order_result.html", status=status, order=order, message=message
        )
    except Exception:
        app.logger.exception("Payment verification failed")
        return render_template(
            "order_result.html", status="pending", order=None,
            message="We could not verify this payment. Please check My Orders."
        )


@app.route("/payment-status/<payment_link_id>")
def payment_status(payment_link_id):
    if not payment_link_id.startswith("plink_"):
        return {"error": "Invalid Payment Link ID."}, 400

    try:
        return verify_payment(payment_link_id)
    except Exception:
        app.logger.exception("Payment status lookup failed")
        return {"error": "Could not verify payment with Razorpay."}, 502


@app.route("/my-orders")
@login_required
def my_orders():
    return render_template(
        "customer_orders.html",
        orders=customer_orders(session["customer_id"])
    )


@app.route("/my-orders/<int:order_id>")
@login_required
def customer_order_detail(order_id):
    order = order_by_id(order_id)

    if not order or order["user_id"] != session["customer_id"]:
        abort(404)

    return render_template(
        "customer_order_detail.html",
        order=order,
        items=order_items(order_id)
    )


# Original ₹499 sample payment endpoint retained for compatibility.
@app.route("/pay", methods=["POST"])
def legacy_pay():
    try:
        link = razorpay_client().payment_link.create({
            "amount": LEGACY_PRICE_PAISE,
            "currency": "INR",
            "accept_partial": False,
            "description": LEGACY_PRODUCT_NAME,
            "reference_id": "SP" + secrets.token_hex(8),
            "reminder_enable": False,
            "notes": {"product": LEGACY_PRODUCT_NAME, "environment": "test"},
        })
        if not link.get("short_url"):
            return "Razorpay did not return a payment URL.", 502
        return redirect(link["short_url"], code=303)
    except Exception:
        app.logger.exception("Legacy payment creation failed")
        return "Could not create payment link.", 502


# =========================================================
# MERCHANT ACCOUNT: FIRST ACCOUNT REGISTRATION + LOGIN
# =========================================================

@app.route("/merchant/login", methods=["GET", "POST"])
def merchant_login():
    if session.get("merchant_logged_in"):
        return redirect(url_for("merchant_dashboard"))

    error = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM merchant_accounts
                WHERE username=? COLLATE NOCASE
            """, (username,)).fetchone()

        merchant = dict(row) if row else None

        if merchant and check_password_hash(merchant["password_hash"], password):
            session.clear()
            session["_csrf_token"] = secrets.token_urlsafe(32)
            session["merchant_logged_in"] = True
            session["merchant_id"] = merchant["id"]
            session["merchant_username"] = merchant["username"]
            return redirect(url_for("merchant_dashboard"))

        error = "Username or password is incorrect."

    return render_template(
        "merchant_login.html",
        error=error,
        registration_open=(merchant_count() == 0)
    )


@app.route("/merchant/register", methods=["GET", "POST"])
def merchant_register():
    if merchant_count() > 0:
        return "Merchant registration is closed because an account already exists.", 403

    error = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirm_password", "")

        valid_username = (
            3 <= len(username) <= 40
            and all(c.isalnum() or c in "._-" for c in username)
        )

        if not valid_username:
            error = "Username must be 3-40 characters using letters, numbers, ., _ or -."
        elif len(password) < 10:
            error = "Password must contain at least 10 characters."
        elif password != confirmation:
            error = "Passwords do not match."
        else:
            try:
                with get_connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    count = conn.execute(
                        "SELECT COUNT(*) FROM merchant_accounts"
                    ).fetchone()[0]

                    if count:
                        raise ValueError("A merchant account already exists.")

                    conn.execute("""
                        INSERT INTO merchant_accounts (username, password_hash)
                        VALUES (?, ?)
                    """, (username, generate_password_hash(password)))

                session.clear()
                session["_csrf_token"] = secrets.token_urlsafe(32)
                session["merchant_logged_in"] = True
                session["merchant_username"] = username

                flash("Merchant account created successfully.", "success")
                return redirect(url_for("merchant_dashboard"))

            except (sqlite3.IntegrityError, ValueError) as exc:
                error = str(exc) or "Could not create this merchant account."

    return render_template("merchant_register.html", error=error)


@app.route("/merchant/logout", methods=["POST"])
@merchant_required
def merchant_logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/merchant")
@merchant_required
def merchant_dashboard():
    stats = dashboard_stats()
    return render_template(
        "merchant_dashboard.html",
        stats=stats,
        orders=all_orders()[:8]
    )


@app.route("/merchant/products")
@merchant_required
def merchant_products():
    return render_template(
        "merchant_products.html",
        products=get_products(include_hidden=True)
    )


def validate_product_form():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    emoji = request.form.get("image_emoji", "📦").strip()[:8] or "📦"

    try:
        price = (Decimal(request.form.get("price", "0")) * 100)
        price_paise = int(price.quantize(Decimal("1")))
        stock = int(request.form.get("stock", "0"))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Enter a valid price and stock value.")

    if not name or len(name) > 120 or not description or len(description) > 1000:
        raise ValueError("Enter a valid product name and description.")
    if price_paise <= 0 or stock < 0 or stock > 1_000_000:
        raise ValueError("Price must be positive and stock cannot be negative.")

    return name, description, price_paise, emoji, stock


@app.route("/merchant/products/new", methods=["GET", "POST"])
@merchant_required
def merchant_product_new():
    error = None

    if request.method == "POST":
        try:
            save_product(*validate_product_form())
            flash("Product added.", "success")
            return redirect(url_for("merchant_products"))
        except ValueError as exc:
            error = str(exc)

    return render_template("product_form.html", product=None, error=error)


@app.route("/merchant/products/<int:product_id>/edit", methods=["GET", "POST"])
@merchant_required
def merchant_product_edit(product_id):
    product = get_product(product_id, include_hidden=True)

    if not product:
        abort(404)

    error = None

    if request.method == "POST":
        try:
            save_product(*validate_product_form(), product_id=product_id)
            flash("Product updated.", "success")
            return redirect(url_for("merchant_products"))
        except ValueError as exc:
            error = str(exc)

    return render_template("product_form.html", product=product, error=error)


@app.route("/merchant/products/<int:product_id>/hide", methods=["POST"])
@merchant_required
def merchant_product_hide(product_id):
    with get_connection() as conn:
        conn.execute("UPDATE products SET is_active=0 WHERE id=?", (product_id,))
    flash("Product hidden from the catalogue.", "success")
    return redirect(url_for("merchant_products"))


@app.route("/merchant/orders")
@merchant_required
def merchant_orders():
    return render_template("merchant_orders.html", orders=all_orders())


@app.route("/merchant/orders/<int:order_id>/status", methods=["POST"])
@merchant_required
def merchant_order_status(order_id):
    try:
        update_fulfilment(order_id, request.form.get("status", ""))
        flash("Order status updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("merchant_orders"))


@app.route("/payment-history")
@merchant_required
def payment_history():
    return render_template("payment_history.html", payments=all_payments())


@app.route("/health")
def health():
    return {
        "app": "SmartPay Commerce",
        "status": "running",
        "razorpay_keys_configured": bool(KEY_ID and KEY_SECRET)
    }


# =========================================================
# RAZORPAY WEBHOOK
# Localhost needs a public HTTPS URL for real webhook delivery.
# =========================================================

@app.route("/razorpay-webhook", methods=["POST"])
def razorpay_webhook():
    if not WEBHOOK_SECRET:
        return {"error": "Webhook secret is not configured."}, 503

    raw_body = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature", "")
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), raw_body, "sha256"
    ).hexdigest()

    if not signature or not hmac.compare_digest(expected, signature):
        return {"error": "Invalid webhook signature."}, 400

    event_data = request.get_json(silent=True) or {}

    if event_data.get("event") == "payment_link.paid":
        entity = (
            event_data.get("payload", {})
            .get("payment_link", {})
            .get("entity", {})
        )
        link_id = entity.get("id", "")

        if link_id.startswith("plink_"):
            try:
                verify_payment(link_id)
            except Exception:
                app.logger.exception("Webhook payment verification failed")
                return {"error": "Payment verification failed."}, 502

    return {"received": True}, 200


# =========================================================
# INITIALIZE DATABASE AND START SERVER
# =========================================================

app.jinja_loader = DictLoader(TEMPLATES)
app.jinja_env.globals["csrf_token"] = csrf_token
initialize_database()


if __name__ == "__main__":
    print("SmartPay Commerce")
    print("Home: http://127.0.0.1:5000/")
    print("Products: http://127.0.0.1:5000/products")
    print("Cart: http://127.0.0.1:5000/cart")
    print("Customer Register: http://127.0.0.1:5000/register")
    print("Customer Login: http://127.0.0.1:5000/login")
    print("My Orders: http://127.0.0.1:5000/my-orders")
    print("Merchant Login: http://127.0.0.1:5000/merchant/login")
    print("Payment History: http://127.0.0.1:5000/payment-history")
    print("Razorpay keys configured:", bool(KEY_ID and KEY_SECRET))

   if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
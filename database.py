
import sqlite3
from pathlib import Path

DATABASE_PATH = Path(__file__).with_name("smartpay.db")


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database():
    with get_connection() as connection:
        # Existing payment history. Do not delete existing records.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_link_id TEXT UNIQUE NOT NULL,
                product_name TEXT NOT NULL,
                amount_paise INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add order_id safely if the existing payments table lacks it.
        payment_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(payments)"
            ).fetchall()
        }

        if "order_id" not in payment_columns:
            connection.execute("""
                ALTER TABLE payments
                ADD COLUMN order_id INTEGER
            """)

        # Product catalogue.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price_paise INTEGER NOT NULL,
                image_emoji TEXT NOT NULL DEFAULT '📦',
                stock INTEGER NOT NULL DEFAULT 10,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Customer orders.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                address TEXT NOT NULL,
                total_paise INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                payment_link_id TEXT UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Products belonging to each order.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                product_name TEXT NOT NULL,
                unit_price_paise INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                line_total_paise INTEGER NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
        """)

        # Add sample products only if the catalogue is empty.
        product_count = connection.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]

        if product_count == 0:
            sample_products = [
                (
                    "Wireless Headphones",
                    "Comfortable wireless headphones for everyday listening.",
                    49900,
                    "🎧",
                    10,
                ),
                (
                    "Power Bank",
                    "Portable power bank for charging devices on the go.",
                    79900,
                    "🔋",
                    10,
                ),
                (
                    "Smart Watch",
                    "A stylish smartwatch for everyday use.",
                    129900,
                    "⌚",
                    10,
                ),
            ]

            connection.executemany("""
                INSERT INTO products (
                    name,
                    description,
                    price_paise,
                    image_emoji,
                    stock
                )
                VALUES (?, ?, ?, ?, ?)
            """, sample_products)


def get_all_products():
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT *
            FROM products
            WHERE is_active = 1
            ORDER BY id
        """).fetchall()

    return [dict(row) for row in rows]


def get_product_by_id(product_id):
    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM products
            WHERE id = ? AND is_active = 1
        """, (product_id,)).fetchone()

    return dict(row) if row else None


def create_order(customer_name, phone, address, cart_data):
    """Create an order using prices and stock from the database."""
    with get_connection() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")

            order_items = []
            total_paise = 0

            for product_id_raw, quantity_raw in cart_data.items():
                try:
                    product_id = int(product_id_raw)
                    quantity = int(quantity_raw)
                except (TypeError, ValueError):
                    raise ValueError("Invalid product or quantity.")

                if quantity < 1:
                    raise ValueError("Quantity must be at least one.")

                product = connection.execute("""
                    SELECT *
                    FROM products
                    WHERE id = ? AND is_active = 1
                """, (product_id,)).fetchone()

                if product is None:
                    raise ValueError("A selected product is unavailable.")

                if quantity > product["stock"]:
                    raise ValueError(
                        f"Not enough stock for {product['name']}."
                    )

                unit_price = int(product["price_paise"])
                line_total = unit_price * quantity

                order_items.append({
                    "product_id": product_id,
                    "product_name": product["name"],
                    "unit_price_paise": unit_price,
                    "quantity": quantity,
                    "line_total_paise": line_total,
                })

                total_paise += line_total

            if not order_items:
                raise ValueError("Your cart is empty.")

            cursor = connection.execute("""
                INSERT INTO orders (
                    customer_name,
                    phone,
                    address,
                    total_paise,
                    status
                )
                VALUES (?, ?, ?, ?, 'pending')
            """, (
                customer_name,
                phone,
                address,
                total_paise,
            ))

            order_id = cursor.lastrowid

            for item in order_items:
                connection.execute("""
                    INSERT INTO order_items (
                        order_id,
                        product_id,
                        product_name,
                        unit_price_paise,
                        quantity,
                        line_total_paise
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    order_id,
                    item["product_id"],
                    item["product_name"],
                    item["unit_price_paise"],
                    item["quantity"],
                    item["line_total_paise"],
                ))

            connection.commit()
            return order_id, total_paise

        except Exception:
            connection.rollback()
            raise


def get_order(order_id):
    
def get_order_by_payment_link(payment_link_id):
    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM orders
            WHERE payment_link_id = ?
        """, (payment_link_id,)).fetchone()

    return dict(row) if row else None

    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM orders
            WHERE id = ?
        """, (order_id,)).fetchone()

    return dict(row) if row else None


def get_order_items(order_id):
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT *
            FROM order_items
            WHERE order_id = ?
            ORDER BY id
        """, (order_id,)).fetchall()

    return [dict(row) for row in rows]


def get_all_orders():
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT *
            FROM orders
            ORDER BY id DESC
        """).fetchall()

    return [dict(row) for row in rows]


def set_order_payment_link(order_id, payment_link_id):
    with get_connection() as connection:
        connection.execute("""
            UPDATE orders
            SET payment_link_id = ?
            WHERE id = ?
        """, (payment_link_id, order_id))


def update_order_status(order_id, status):
    allowed_statuses = {
        "pending",
        "paid",
        "failed",
        "cancelled",
    }

    if status not in allowed_statuses:
        raise ValueError("Unsupported order status.")

    with get_connection() as connection:
        connection.execute("""
            UPDATE orders
            SET status = ?
            WHERE id = ?
        """, (status, order_id))


def save_payment(
    payment_link_id,
    product_name,
    amount_paise,
    status,
    order_id=None,
):
    with get_connection() as connection:
        connection.execute("""
            INSERT INTO payments (
                payment_link_id,
                product_name,
                amount_paise,
                status,
                order_id
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(payment_link_id) DO UPDATE SET
                status = excluded.status,
                amount_paise = excluded.amount_paise,
                order_id = COALESCE(
                    excluded.order_id,
                    payments.order_id
                )
        """, (
            payment_link_id,
            product_name,
            amount_paise,
            status,
            order_id,
        ))


def get_all_payments():
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT *
            FROM payments
            ORDER BY id DESC
        """).fetchall()

    return [dict(row) for row in rows]

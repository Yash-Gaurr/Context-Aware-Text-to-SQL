"""
setup_db.py
-----------
Creates and seeds the SQLite database used by the Text-to-SQL assistant.

Why a fixed "today"?
The whole point of this project is to demonstrate ambiguity handling around
words like "recent" and "last month". For that demo to be reproducible for
anyone running this project (and during the viva), all seed data is generated
relative to a fixed reference date (REFERENCE_TODAY) instead of the real
system clock. The application also treats REFERENCE_TODAY as "today" when
resolving relative time phrases, so results are 100% repeatable.

Run directly to (re)build the database:
    python database/setup_db.py
"""

import sqlite3
import random
import os
from datetime import date, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "ecommerce.db")

# Fixed reference date used everywhere in the app instead of date.today()
REFERENCE_TODAY = date(2026, 9, 7)

SCHEMA_SQL = """
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    city          TEXT NOT NULL,
    signup_date   TEXT NOT NULL
);

CREATE TABLE products (
    product_id    INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    price         REAL NOT NULL
);

CREATE TABLE orders (
    order_id      INTEGER PRIMARY KEY,
    customer_id   INTEGER NOT NULL,
    order_date    TEXT NOT NULL,
    status        TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL,
    product_id    INTEGER NOT NULL,
    quantity      INTEGER NOT NULL,
    unit_price    REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders (order_id),
    FOREIGN KEY (product_id) REFERENCES products (product_id)
);
"""

CITIES = ["Mumbai", "Delhi", "Bangalore", "Pune", "Chennai", "Aligarh", "Hyderabad", "Kolkata"]
FIRST_NAMES = ["Aarav", "Vivaan", "Ishaan", "Kabir", "Rohan", "Ananya", "Diya", "Priya",
               "Sneha", "Meera", "Arjun", "Kunal", "Neha", "Riya", "Aditya"]
LAST_NAMES = ["Sharma", "Verma", "Gupta", "Iyer", "Khan", "Reddy", "Patel", "Singh", "Nair", "Das"]

PRODUCTS = [
    ("Wireless Mouse", "Electronics", 599.0),
    ("Mechanical Keyboard", "Electronics", 2999.0),
    ("USB-C Hub", "Electronics", 1499.0),
    ("Bluetooth Speaker", "Electronics", 1999.0),
    ("Cotton T-Shirt", "Apparel", 499.0),
    ("Denim Jacket", "Apparel", 2499.0),
    ("Running Shoes", "Apparel", 3499.0),
    ("Ceramic Mug Set", "Home", 799.0),
    ("Table Lamp", "Home", 1299.0),
    ("Non-Stick Pan", "Home", 899.0),
    ("Novel: The Silent Hour", "Books", 349.0),
    ("Data Structures Handbook", "Books", 599.0),
]

STATUSES = ["placed", "shipped", "delivered", "cancelled"]


def build_database(db_path: str = DB_PATH, seed: int = 42) -> None:
    random.seed(seed)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(SCHEMA_SQL)

    # --- customers -----------------------------------------------------
    customers = []
    for i in range(1, 16):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        name = f"{first} {last}"
        email = f"{first.lower()}.{last.lower()}{i}@example.com"
        city = random.choice(CITIES)
        signup = REFERENCE_TODAY - timedelta(days=random.randint(60, 900))
        customers.append((i, name, email, city, signup.isoformat()))
    cur.executemany(
        "INSERT INTO customers (customer_id, name, email, city, signup_date) VALUES (?, ?, ?, ?, ?)",
        customers,
    )

    # --- products --------------------------------------------------------
    products = [(i + 1, *p) for i, p in enumerate(PRODUCTS)]
    cur.executemany(
        "INSERT INTO products (product_id, name, category, price) VALUES (?, ?, ?, ?)",
        products,
    )

    # --- orders + order_items --------------------------------------------
    # Spread orders across the last ~5 months relative to REFERENCE_TODAY so
    # that "this month", "last month" and "recent" all have real data.
    order_id = 1
    order_item_id = 1
    orders_rows = []
    items_rows = []
    for _ in range(90):
        days_back = random.randint(0, 150)
        order_date = REFERENCE_TODAY - timedelta(days=days_back)
        customer_id = random.randint(1, 15)
        status = random.choices(STATUSES, weights=[10, 20, 60, 10])[0]
        orders_rows.append((order_id, customer_id, order_date.isoformat(), status))

        num_items = random.randint(1, 3)
        chosen_products = random.sample(products, num_items)
        for prod in chosen_products:
            product_id, _, _, price = prod[0], prod[1], prod[2], prod[3]
            quantity = random.randint(1, 4)
            items_rows.append((order_item_id, order_id, product_id, quantity, price))
            order_item_id += 1
        order_id += 1

    cur.executemany(
        "INSERT INTO orders (order_id, customer_id, order_date, status) VALUES (?, ?, ?, ?)",
        orders_rows,
    )
    cur.executemany(
        "INSERT INTO order_items (order_item_id, order_id, product_id, quantity, unit_price) "
        "VALUES (?, ?, ?, ?, ?)",
        items_rows,
    )

    conn.commit()
    conn.close()
    print(f"Database built at {db_path} "
          f"({len(customers)} customers, {len(products)} products, "
          f"{len(orders_rows)} orders, {len(items_rows)} order items)")


if __name__ == "__main__":
    build_database()

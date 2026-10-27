import json
import sqlite3
import time
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    asin           TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    times_ordered  INTEGER NOT NULL DEFAULT 0,   -- number of orders containing it
    total_qty      INTEGER NOT NULL DEFAULT 0,
    usual_qty      INTEGER NOT NULL DEFAULT 1,   -- most common qty per order
    buy_asin       TEXT,                         -- ASIN to open on Amazon if different from asin (CSV imports)
    price          TEXT,                         -- latest known price as shown, e.g. "£2.00" (CSV imports)
    last_ordered   TEXT
);
-- one row per (order, asin) so re-running the scraper is idempotent
CREATE TABLE IF NOT EXISTS order_lines (
    order_id   TEXT NOT NULL,
    asin       TEXT NOT NULL,
    qty        INTEGER NOT NULL,
    order_date TEXT,
    PRIMARY KEY (order_id, asin)
);
-- learned "what I type" -> "what I mean"
CREATE TABLE IF NOT EXISTS aliases (
    phrase TEXT PRIMARY KEY,
    asin   TEXT NOT NULL
);
-- a question the bot asked and is waiting on ("reply 1, 2 or 3")
CREATE TABLE IF NOT EXISTS pending (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,  -- answered oldest-first
    chat_id  TEXT NOT NULL,
    phrase   TEXT NOT NULL,
    qty      INTEGER NOT NULL,
    options  TEXT NOT NULL,          -- json [{asin, title}]
    prompt   TEXT NOT NULL,
    created  REAL NOT NULL
);
-- "how many more?" asked after an add; a bare number in reply adds that many more
CREATE TABLE IF NOT EXISTS followups (
    chat_id  TEXT PRIMARY KEY,
    asin     TEXT NOT NULL,
    created  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id  TEXT NOT NULL,
    sender   TEXT NOT NULL,          -- display name, or 'bot'
    body     TEXT NOT NULL,
    ts       REAL NOT NULL
);
-- audit log of everything we put in the basket
CREATE TABLE IF NOT EXISTS cart_adds (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    asin    TEXT NOT NULL,
    title   TEXT NOT NULL,
    qty     INTEGER NOT NULL,
    sender  TEXT,
    ts      REAL NOT NULL
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(products)")}
        if "buy_asin" not in cols:  # DBs created before the column existed
            c.execute("ALTER TABLE products ADD COLUMN buy_asin TEXT")
        if "price" not in cols:
            c.execute("ALTER TABLE products ADD COLUMN price TEXT")


def set_buy_asin(asin: str, buy_asin: str):
    with connect() as c:
        c.execute("UPDATE products SET buy_asin = ? WHERE asin = ?", (buy_asin, asin))


def set_price(asin: str, price: str):
    with connect() as c:
        c.execute("UPDATE products SET price = ? WHERE asin = ?", (price, asin))


def all_products() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM products ORDER BY times_ordered DESC")]


def get_product(asin: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM products WHERE asin = ?", (asin,)).fetchone()
        return dict(r) if r else None


def upsert_order_line(order_id: str, asin: str, title: str, qty: int, order_date: str | None):
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO order_lines VALUES (?, ?, ?, ?)", (order_id, asin, qty, order_date)
        )
        c.execute(
            "INSERT INTO products (asin, title) VALUES (?, ?) ON CONFLICT(asin) DO UPDATE SET title = excluded.title",
            (asin, title),
        )


def recompute_product_stats():
    with connect() as c:
        c.execute("""
            UPDATE products SET
              times_ordered = (SELECT COUNT(*) FROM order_lines o WHERE o.asin = products.asin),
              total_qty     = (SELECT COALESCE(SUM(qty), 0) FROM order_lines o WHERE o.asin = products.asin),
              last_ordered  = (SELECT MAX(order_date) FROM order_lines o WHERE o.asin = products.asin),
              usual_qty     = COALESCE((SELECT qty FROM order_lines o WHERE o.asin = products.asin
                                        GROUP BY qty ORDER BY COUNT(*) DESC, qty LIMIT 1), 1)
        """)


def upsert_product(asin: str, title: str):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO products (asin, title) VALUES (?, ?)", (asin, title))


def get_alias(phrase: str) -> str | None:
    with connect() as c:
        r = c.execute("SELECT asin FROM aliases WHERE phrase = ?", (phrase,)).fetchone()
        return r["asin"] if r else None


def set_alias(phrase: str, asin: str):
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO aliases VALUES (?, ?)", (phrase, asin))


def add_pending(chat_id: str, phrase: str, qty: int, options: list[dict], prompt: str):
    with connect() as c:
        c.execute(
            "INSERT INTO pending (chat_id, phrase, qty, options, prompt, created) VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, phrase, qty, json.dumps(options), prompt, time.time()),
        )


def peek_pending(chat_id: str, max_age_s: int = 3600) -> dict | None:
    """Oldest unanswered question for this chat (expired ones are dropped)."""
    with connect() as c:
        c.execute("DELETE FROM pending WHERE created < ?", (time.time() - max_age_s,))
        r = c.execute("SELECT * FROM pending WHERE chat_id = ? ORDER BY id LIMIT 1", (chat_id,)).fetchone()
        if not r:
            return None
        return {**dict(r), "options": json.loads(r["options"])}


def delete_pending(pending_id: int):
    with connect() as c:
        c.execute("DELETE FROM pending WHERE id = ?", (pending_id,))


def set_followup(chat_id: str, asin: str):
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO followups VALUES (?, ?, ?)", (chat_id, asin, time.time()))


def pop_followup(chat_id: str, max_age_s: int = 3600) -> str | None:
    """The ASIN we last asked "how many more?" about; cleared once read."""
    with connect() as c:
        r = c.execute("SELECT * FROM followups WHERE chat_id = ?", (chat_id,)).fetchone()
        c.execute("DELETE FROM followups WHERE chat_id = ?", (chat_id,))
        return r["asin"] if r and r["created"] >= time.time() - max_age_s else None


def log_message(chat_id: str, sender: str, body: str) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO messages (chat_id, sender, body, ts) VALUES (?, ?, ?, ?)",
            (chat_id, sender, body, time.time()),
        )
        return cur.lastrowid


def messages_after(chat_id: str, after_id: int) -> list[dict]:
    with connect() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM messages WHERE chat_id = ? AND id > ? ORDER BY id", (chat_id, after_id)
            )
        ]


def log_cart_add(asin: str, title: str, qty: int, sender: str):
    with connect() as c:
        c.execute(
            "INSERT INTO cart_adds (asin, title, qty, sender, ts) VALUES (?, ?, ?, ?, ?)",
            (asin, title, qty, sender, time.time()),
        )


def last_cart_add() -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM cart_adds ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None


def delete_cart_add(add_id: int):
    with connect() as c:
        c.execute("DELETE FROM cart_adds WHERE id = ?", (add_id,))

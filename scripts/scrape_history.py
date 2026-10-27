"""Scrape Amazon Fresh order history into the products table.

    .venv/bin/python scripts/scrape_history.py 2024 2025 2026

Safe to re-run: order lines are keyed by (order_id, asin).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grocerybot import db  # noqa: E402
from grocerybot.amazon import PlaywrightAmazon  # noqa: E402


async def main(years: list[int]):
    db.init()
    amazon = PlaywrightAmazon()
    await amazon.start()
    n = 0
    try:
        async for order_id, date, asin, title, qty in amazon.scrape_order_history(years):
            db.upsert_order_line(order_id, asin, title, qty, date)
            n += 1
            print(f"{order_id}  {qty}x  {title[:70]}")
    finally:
        await amazon.stop()
        db.recompute_product_stats()
    print(f"\n{n} order lines, {len(db.all_products())} distinct products")


asyncio.run(main([int(y) for y in sys.argv[1:]] or [2025, 2026]))

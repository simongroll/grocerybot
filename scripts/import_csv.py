"""Import a list of regularly ordered items as product history.

    .venv/bin/python scripts/import_csv.py ~/Downloads/my-items.csv

Expects columns: Item Name (or Product Title), ASIN, and optionally Quantity, which
becomes the item's usual quantity (default 1), and Price, which is shown when the bot asks
"which one?". The URL column is ignored; the product page is always $AMAZON_BASE_URL/dp/<ASIN>. An ASIN shared by several
differently-named items can't be the product's key, so those items get a placeholder key
but keep the CSV ASIN as `buy_asin`, which is what gets opened on Amazon. Adds are refused if the page title doesn't match.
"""

import csv
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grocerybot import config, db  # noqa: E402


def placeholder(title: str) -> str:
    return "X" + hashlib.sha1(title.lower().encode()).hexdigest()[:9].upper()


def unmangle(title: str) -> str:
    """Undo UTF-8 read as Mac Roman (Excel on macOS): "570g ‚Äì High" -> "570g – High"."""
    try:
        return title.encode("mac_roman").decode("utf-8")
    except UnicodeError:
        return title


def main(path: str):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f)]
    for r in rows:
        r["Item Name"] = unmangle((r.get("Item Name") or r.get("Product Title") or "").strip())
        r["ASIN"] = (r.get("ASIN") or "").strip()
    # skips blank rows and totals rows (spreadsheet exports pad with both)
    rows = [r for r in rows if r["Item Name"] and r["ASIN"]]

    titles_by_asin = defaultdict(set)
    for r in rows:
        titles_by_asin[r["ASIN"]].add(r["Item Name"])

    db.init()
    verified = unverified = 0
    for r in rows:
        title, asin = r["Item Name"], r["ASIN"]
        qty = int(r.get("Quantity") or 1)
        ok = len(asin) == 10 and len(titles_by_asin[asin]) == 1
        key = asin if ok else placeholder(title)
        db.upsert_order_line(f"CSV:{Path(path).name}", key, title, qty, None)
        db.set_buy_asin(key, asin)
        if price := (r.get("Price") or "").strip():
            db.set_price(key, price)
        verified += ok
        unverified += not ok
    db.recompute_product_stats()

    print(f"Imported {len(rows)} items into {config.DB_PATH}")
    print(f"  {verified} with a unique ASIN, {unverified} sharing an ASIN with other items (likely wrong; adds are title-checked)")


if __name__ == "__main__":
    main(sys.argv[1])

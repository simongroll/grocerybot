"""Fake order history so you can try the chat before scraping anything."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grocerybot import db  # noqa: E402

PRODUCTS = [
    ("B07MOCK001", "Amazon Organic Semi Skimmed Milk 2L", 2, 24),
    ("B07MOCK002", "Amazon Organic Whole Milk 2L", 1, 3),
    ("B07MOCK003", "Cravendale Filtered Semi Skimmed Milk 2L", 1, 2),
    ("B07MOCK004", "Oatly Oat Drink Barista Edition 1L", 2, 10),
    ("B07MOCK005", "Bananas Loose x5", 1, 30),
    ("B07MOCK006", "Organic Bananas Fairtrade x5", 1, 4),
    ("B07MOCK007", "Free Range Large Eggs x12", 1, 20),
    ("B07MOCK008", "Clarence Court Burford Brown Eggs x6", 1, 3),
    ("B07MOCK009", "Hovis Seed Sensations Seven Seeds Bread 800g", 1, 15),
    ("B07MOCK010", "Warburtons Toastie White Bread 800g", 1, 6),
    ("B07MOCK011", "Lurpak Slightly Salted Spreadable 500g", 1, 12),
    ("B07MOCK012", "Cathedral City Mature Cheddar 550g", 1, 9),
    ("B07MOCK013", "Heinz Baked Beans 4 x 415g", 1, 7),
    ("B07MOCK014", "Avocados Ripe & Ready x2", 2, 14),
    ("B07MOCK015", "Fage Total 0% Fat Free Greek Yogurt 500g", 2, 11),
    ("B07MOCK016", "Yorkshire Tea 160 Bags", 1, 5),
    ("B07MOCK017", "Andrex Classic Clean Toilet Tissue 9 Rolls", 1, 6),
    ("B07MOCK018", "Fairy Original Washing Up Liquid 780ml", 1, 4),
    ("B07MOCK019", "Chicken Breast Fillets 1kg", 1, 13),
    ("B07MOCK020", "Baby Spinach 240g", 1, 9),
]

db.init()
for asin, title, usual, times in PRODUCTS:
    for i in range(times):
        db.upsert_order_line(f"MOCK-{asin}-{i}", asin, title, usual if random.random() > 0.2 else usual + 1, "2026-10-01")
db.recompute_product_stats()
print(f"Seeded {len(PRODUCTS)} products.")

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# GROCERYBOT_ENV_FILE=.env.test picks an alternative config (real env vars still win)
load_dotenv(ROOT / os.getenv("GROCERYBOT_ENV_FILE", ".env"))

DATA_DIR = ROOT / os.getenv("GROCERYBOT_DATA_DIR", "data")  # relative paths are relative to the repo
DB_PATH = DATA_DIR / "grocerybot.db"
# Shared across test/live data dirs so you only log in to Amazon once
BROWSER_PROFILE_DIR = ROOT / os.getenv("BROWSER_PROFILE_DIR", "data/browser-profile")

# "mock" = fake basket, no browser. "playwright" = real Amazon via persistent Chromium profile.
AMAZON_BACKEND = os.getenv("AMAZON_BACKEND", "mock")
AMAZON_BASE_URL = os.getenv("AMAZON_BASE_URL", "https://www.amazon.co.uk").rstrip("/")
# base64("Amazon Fresh") - the storefront id Amazon uses in Fresh URLs
FRESH_BRAND_ID = os.getenv("FRESH_BRAND_ID", "QW1hem9uIEZyZXNo")
HEADLESS = os.getenv("HEADLESS", "1") == "1"
# Browser locale; match your Amazon store so pages render the way the selectors expect
LOCALE = os.getenv("LOCALE", "en-GB")

# Matching: LLM is optional. Without ANTHROPIC_API_KEY we fall back to pure fuzzy matching.
LLM_ENABLED = bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("LLM_ENABLED", "1") == "1"
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-5-5")

# Fuzzy score (0-100) above which we skip the LLM and add straight away.
AUTO_ADD_SCORE = int(os.getenv("AUTO_ADD_SCORE", "92"))

DATA_DIR.mkdir(parents=True, exist_ok=True)

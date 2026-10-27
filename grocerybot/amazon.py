"""Amazon Fresh adapters.

There is no public Amazon Fresh cart API, so the real adapter drives a Chromium
browser via Playwright using a *persistent profile* (cookies survive restarts).
You log in once with `scripts/login.py`; after that the bot runs headless.

All selectors below are best-effort and WILL need tweaking against the live
site — Amazon changes markup often and differs between .com / .co.uk / .de.
Run with HEADLESS=0 to watch what it does.
"""

import asyncio
import random
import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from . import config, db

ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


@dataclass
class AddResult:
    ok: bool
    title: str = ""
    error: str = ""


class SessionExpired(Exception):
    pass


class MockAmazon:
    """In-memory basket. Lets you test the whole chat flow with no Amazon account."""

    def __init__(self):
        self.basket: dict[str, int] = {}

    async def start(self): ...
    async def stop(self): ...

    async def is_logged_in(self) -> bool:
        return True

    async def add_to_cart(self, asin: str, qty: int, expected_title: str | None = None) -> AddResult:
        await asyncio.sleep(0.4)  # pretend a browser is doing something
        self.basket[asin] = self.basket.get(asin, 0) + qty
        return AddResult(True, title=expected_title or asin)

    async def remove_from_cart(self, asin: str, qty: int) -> bool:
        if asin in self.basket:
            self.basket[asin] = max(0, self.basket[asin] - qty)
        return True

    async def search(self, query: str, n: int = 3) -> list[dict]:
        return [{"asin": f"S{abs(hash(query)) % 10**6:06d}{i:03d}", "title": f"{query.title()} (search result {i + 1})"} for i in range(n)]

    async def basket_summary(self) -> list[str]:
        return [f"{q}x {db.get_product(a)['title'] if db.get_product(a) else a}" for a, q in self.basket.items() if q]


class PlaywrightAmazon:
    def __init__(self):
        self._pw = None
        self.ctx = None
        self.lock = asyncio.Lock()  # one browser, one action at a time

    async def start(self):
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self.ctx = await self._pw.chromium.launch_persistent_context(
            str(config.BROWSER_PROFILE_DIR),
            headless=config.HEADLESS,
            viewport={"width": 1280, "height": 900},
            locale=config.LOCALE,
        )

    async def stop(self):
        if self.ctx:
            await self.ctx.close()
        if self._pw:
            await self._pw.stop()

    async def _page(self):
        return self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()

    async def _check_session(self, page):
        if "/ap/signin" in page.url or await page.locator("#ap_email, #ap_password").count():
            raise SessionExpired()

    async def is_logged_in(self) -> bool:
        async with self.lock:
            page = await self._page()
            await page.goto(f"{config.AMAZON_BASE_URL}/gp/css/homepage.html", wait_until="domcontentloaded")
            return "/ap/signin" not in page.url

    async def add_to_cart(self, asin: str, qty: int, expected_title: str | None = None) -> AddResult:
        async with self.lock:
            page = await self._page()
            await page.goto(f"{config.AMAZON_BASE_URL}/gp/product/{asin}", wait_until="domcontentloaded")
            await self._check_session(page)
            if not await page.locator("#productTitle").count():
                return AddResult(False, expected_title or "", f"ASIN {asin} has no product page (no longer sold?)")
            title = (await page.locator("#productTitle").first.inner_text(timeout=8000)).strip()
            # Guard against wrong ASINs (e.g. from a bad CSV export) putting the wrong thing in the basket.
            if expected_title and fuzz.token_set_ratio(expected_title.lower(), title.lower()) < 50:
                return AddResult(False, title, f"ASIN {asin} is “{title}”, not “{expected_title}” — not added")

            # Only look inside this product's buy box: the page also renders the regular basket's
            # side panel, whose +/- steppers belong to other products.
            box = page.locator("#addToCart, #desktop_almBuyBox, #buybox").first
            # Fresh pages render their own add button; the regular retail one is the fallback.
            button = box.locator(
                "#freshAddToCartButton input, #freshAddToCartButton button, "
                "#add-to-cart-button, input[name='submit.addToCart']"
            ).first
            if not await button.count():
                return AddResult(False, title, "no add-to-cart button (out of stock or not on Fresh?)")
            if qty > 1 and not await self._pick_qty(page, box, qty):
                return AddResult(False, title, f"couldn't set quantity to {qty} — nothing added")

            before = await self._cart_count(page)
            await button.click()
            # Fresh items go in a separate basket that #nav-cart-count doesn't include, so the
            # "Added to basket" panel is the real signal; the count is the fallback for retail adds.
            try:
                await page.locator("text=/Added to (basket|cart)/i >> visible=true").first.wait_for(state="visible", timeout=8000)
                return AddResult(True, title)
            except Exception:
                pass
            after = await self._cart_count(page)
            if before is not None and after is not None and after > before:
                return AddResult(True, title)
            return AddResult(False, title, "no “Added to basket” confirmation — check the basket")

    async def _pick_qty(self, page, box, qty: int) -> bool:
        """Set the buy box's quantity picker before adding. False if it can't (nothing is added then)."""
        select = box.locator("select#quantity")  # regular retail page
        if await select.count():
            try:
                await select.select_option(str(qty))
                return True
            except Exception:
                return False
        # Fresh "Qty: 1" widget; its option list is rendered elsewhere in the page, keyed by the widget id
        widget = box.locator("button[id^='qs-widget-button-'][id$='-announce']").first
        if not await widget.count():
            return False
        uid = (await widget.get_attribute("id"))[len("qs-widget-button-") : -len("-announce")]
        await widget.click()
        item = page.locator(f"#qs-widget-dropdown-item-{qty}-{uid}")
        try:
            await item.wait_for(state="visible", timeout=3000)
        except Exception:
            await page.keyboard.press("Escape")
            return False
        await item.click()
        await page.wait_for_timeout(500)
        return (await widget.inner_text()).strip().endswith(f" {qty}")

    async def remove_from_cart(self, asin: str, qty: int) -> bool:
        # TODO: open /cart, find row with data-asin, decrement qty. Left for v2.
        return False

    async def _cart_count(self, page) -> int | None:
        try:
            return int((await page.locator("#nav-cart-count").first.inner_text(timeout=3000)).strip())
        except Exception:
            return None

    async def search(self, query: str, n: int = 3) -> list[dict]:
        async with self.lock:
            page = await self._page()
            await page.goto(
                f"{config.AMAZON_BASE_URL}/s?k={query}&i=amazonfresh&almBrandId={config.FRESH_BRAND_ID}",
                wait_until="domcontentloaded",
            )
            await self._check_session(page)
            out = []
            for card in await page.locator("div[data-component-type='s-search-result'][data-asin]").all():
                asin = await card.get_attribute("data-asin")
                title = (await card.locator("h2").first.inner_text()).strip()
                if asin and title:
                    out.append({"asin": asin, "title": title})
                if len(out) >= n:
                    break
            return out

    async def basket_summary(self) -> list[str]:
        return []

    # --- history scrape -------------------------------------------------------------

    async def scrape_order_history(self, years: list[int], fresh_only: bool = True):
        """Yield (order_id, order_date, asin, title, qty). Visits each order's detail
        page because big Fresh orders only show a few thumbnails in the list view."""
        page = await self._page()
        order_ids: list[tuple[str, str]] = []
        for year in years:
            start = 0
            while True:
                await page.goto(
                    f"{config.AMAZON_BASE_URL}/your-orders/orders?timeFilter=year-{year}&startIndex={start}",
                    wait_until="domcontentloaded",
                )
                await self._check_session(page)
                cards = await page.locator(".order-card, .js-order-card").all()
                if not cards:
                    break
                for card in cards:
                    text = await card.inner_text()
                    if fresh_only and "fresh" not in text.lower():
                        continue
                    m = re.search(r"\b(\d{3}-\d{7}-\d{7})\b", text)
                    d = re.search(r"(\d{1,2} \w+ \d{4}|\w+ \d{1,2}, \d{4})", text)
                    if m:
                        order_ids.append((m.group(1), d.group(1) if d else ""))
                start += 10
                await page.wait_for_timeout(1500 + random.randint(0, 1500))

        for order_id, date in dict.fromkeys(order_ids):
            await page.goto(
                f"{config.AMAZON_BASE_URL}/gp/your-account/order-details?orderID={order_id}",
                wait_until="domcontentloaded",
            )
            await self._check_session(page)
            seen = set()
            for link in await page.locator("a[href*='/dp/'], a[href*='/gp/product/']").all():
                href = await link.get_attribute("href") or ""
                m = ASIN_RE.search(href)
                title = (await link.inner_text()).strip()
                if not m or not title or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                # quantity is shown either as a badge on the image or as "Qty: 2" near the title
                row_text = await link.evaluate(
                    "el => (el.closest('.a-fixed-left-grid, .yohtmlc-item, [class*=item]') || el.parentElement).innerText"
                )
                q = re.search(r"(?:Qty|Quantity)[:\s]*(\d+)", row_text, re.I)
                yield order_id, date, m.group(1), title, int(q.group(1)) if q else 1
            await page.wait_for_timeout(1500 + random.randint(0, 1500))


def make_client():
    return PlaywrightAmazon() if config.AMAZON_BACKEND == "playwright" else MockAmazon()

"""One-off: open a real (headed) browser on the persistent profile so you can log in
to Amazon, including 2FA. Close the window when you see your account page.

On a VPS run it under a virtual display you can see, e.g.
    ssh -X vps 'cd grocerybot && .venv/bin/python scripts/login.py'
or Xvfb + x11vnc / noVNC. See ARCHITECTURE.md.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from grocerybot import config  # noqa: E402


async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(str(config.BROWSER_PROFILE_DIR), headless=False, locale=config.LOCALE)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(f"{config.AMAZON_BASE_URL}/gp/css/homepage.html")
        print("Log in (tick 'Keep me signed in'), then close the browser window.")
        await ctx.wait_for_event("close", timeout=0)


asyncio.run(main())

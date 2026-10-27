"""Move the Amazon login from one machine's browser profile to another's.

Logging in needs a visible browser, which is awkward on a headless box like the Pi.
Instead, log in on a computer with a screen (scripts/login.py), then copy the Amazon cookies across:

    .venv/bin/python scripts/session.py export amazon-session.json     # on the computer you logged in on
    python scripts/session.py import amazon-session.json               # on the Pi

The server must be stopped on both sides: a browser profile can only be open once.
The file is as good as your Amazon password; delete it after importing.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from grocerybot import config  # noqa: E402


async def main(cmd: str, path: str):
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(str(config.BROWSER_PROFILE_DIR), headless=True, locale=config.LOCALE)
        try:
            if cmd == "export":
                cookies = [c for c in (await ctx.storage_state())["cookies"] if "amazon." in c["domain"]]
                Path(path).write_text(json.dumps(cookies))
                os.chmod(path, 0o600)
                print(f"Exported {len(cookies)} Amazon cookies to {path}")
            else:
                cookies = json.loads(Path(path).read_text())
                await ctx.add_cookies(cookies)
                print(f"Imported {len(cookies)} Amazon cookies into {config.BROWSER_PROFILE_DIR}")
        finally:
            await ctx.close()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("export", "import"):
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))

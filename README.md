# grocerybot

Text "low fat milk" to a WhatsApp number and it goes into your Amazon Fresh basket.

```
you:        milk, 2 bananas, eggs x2
grocerybot: ✅ Added 2x Organic Semi Skimmed Milk 2L to basket
            ✅ Added 2x Bananas Loose x5 to basket
            ✅ Added 2x Free Range Large Eggs x12 to basket
```

- **Learns what you buy.** It imports your order history and matches "milk" to the milk
  *you* buy, in the quantity you usually buy.
- **Asks when unsure.** "Which milk? 1/2/3", and it remembers your answer next time.
- **Optional LLM matching** with Claude for synonyms ("low fat" → "semi-skimmed"). Without an
  API key it uses fuzzy matching only.
- **Several people, one basket.** Everyone on the allowlist messages the bot and fills the same basket.
- Commands: `undo`, `basket`, `help`.

Amazon has no API for this, so grocerybot drives a real Chromium browser that is logged in to
your account. See [ARCHITECTURE.md](ARCHITECTURE.md) for how it works.

> [!WARNING]
> Automating Amazon is against Amazon's Conditions of Use. Run this only on your own account,
> at personal scale, at your own risk. The browser profile in `data/` holds a logged-in Amazon
> session: treat it like your password. Not affiliated with Amazon, Meta or Twilio.

Built and tested against **amazon.co.uk**. Other Amazon stores may need selector tweaks in
`grocerybot/amazon.py`.

## 1. Try it with a mock basket (no Amazon needed)

Requires Python 3.11+.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python scripts/seed_mock.py          # fake order history
.venv/bin/uvicorn grocerybot.server:app --reload --port 8765
open http://localhost:8765
```

Try `low fat milk`, `2 bananas, eggs x2` and `dragonfruit`, then `1` / `none`, and `undo`.
Add `ANTHROPIC_API_KEY` to `.env` to turn on LLM matching.

## 2. Connect your Amazon account

Set `AMAZON_BASE_URL` (and `LOCALE`) in `.env` to your store, then:

```bash
.venv/bin/playwright install chromium
.venv/bin/python scripts/login.py               # log in once, tick "Keep me signed in", close the window
rm -f data/grocerybot.db                        # drop the mock data
```

Load your product history in one of two ways:

```bash
# a) scrape Amazon Fresh order history
.venv/bin/python scripts/scrape_history.py 2025 2026

# b) or import a CSV with columns: Item Name, ASIN, and optionally Quantity, Price
.venv/bin/python scripts/import_csv.py ~/Downloads/my-items.csv
```

Run against the real basket. `HEADLESS=0` shows the browser, which helps on a first run:

```bash
AMAZON_BACKEND=playwright HEADLESS=0 .venv/bin/uvicorn grocerybot.server:app --port 8765
```

To test without touching your main database, put the settings in a separate env file
(e.g. `.env.test` with `GROCERYBOT_DATA_DIR=data/test`) and run with `GROCERYBOT_ENV_FILE=.env.test`.

## 3. WhatsApp via Twilio

1. Create a Twilio account and join the [WhatsApp sandbox](https://www.twilio.com/docs/whatsapp/sandbox)
   from every phone that will use the bot.
2. Expose the server publicly, e.g. `ngrok http --url=<your-domain>.ngrok-free.app 8765`.
3. In the sandbox settings, set *When a message comes in* to `https://<your-domain>/twilio/whatsapp` (POST).
4. Fill in `.env`:
   ```
   TWILIO_ACCOUNT_SID=...
   TWILIO_AUTH_TOKEN=...
   TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
   PUBLIC_BASE_URL=https://<your-domain>
   ALLOWED_WHATSAPP_NUMBERS=+447700900000,+447700900001
   ```

Only signed requests from Twilio and allowlisted numbers are accepted. The web chat is served
only to direct localhost requests, never through the tunnel.

For a dedicated number instead of the sandbox, register a WhatsApp sender with Twilio's
[Self Sign-up](https://www.twilio.com/docs/whatsapp/self-sign-up) and update `TWILIO_WHATSAPP_FROM`.
WhatsApp group chats aren't supported by Twilio; each person messages the bot directly.

## 4. Run it 24/7 on a Raspberry Pi (optional)

A home machine works best: Amazon sees your usual residential IP, so you get fewer captchas
and logouts than from a cloud server. `scripts/deploy_pi.sh` copies the code to a Pi, builds
a Docker image and runs the bot plus an ngrok container, both restarting automatically.

On the Pi, once: install Docker, put the config in `~/grocerybot/.env.prod` (the script also
copies a local `.env.prod`), and put `NGROK_AUTHTOKEN=...` in `~/grocerybot/ngrok.env`. Then from your computer:

```bash
PI=pi@raspberrypi.local scripts/deploy_pi.sh
```

Logging in needs a visible browser, so log in on your computer and copy the session across
(stop the server on both sides first):

```bash
.venv/bin/python scripts/session.py export amazon-session.json
scp amazon-session.json pi@raspberrypi.local:grocerybot/data/ && rm amazon-session.json
ssh pi@raspberrypi.local 'cd grocerybot && docker stop grocerybot && docker run --rm \
    --platform linux/arm64 --security-opt seccomp=unconfined --init --ipc host \
    -v $PWD/data:/app/data -v $PWD/.env.prod:/app/.env.prod:ro grocerybot \
    python scripts/session.py import data/amazon-session.json \
    && rm data/amazon-session.json && docker start grocerybot'
```

The script targets a 64-bit-kernel Pi (it runs arm64 containers); see the comments at the top
of the script for why the image is built the unusual way.

## Configuration

All settings live in `.env` (see [.env.example](.env.example)). Set `GROCERYBOT_ENV_FILE` to use a different file.

| Variable | Default | |
|---|---|---|
| `AMAZON_BACKEND` | `mock` | `mock` or `playwright` |
| `AMAZON_BASE_URL` | `https://www.amazon.co.uk` | Your Amazon store |
| `LOCALE` | `en-GB` | Browser locale |
| `HEADLESS` | `1` | `0` shows the browser |
| `ANTHROPIC_API_KEY` | | Turns on LLM matching |
| `LLM_MODEL` | `claude-opus-5-5` | `claude-haiku-5-5` is much cheaper and usually enough |
| `AUTO_ADD_SCORE` | `92` | Fuzzy score above which items are added without asking |
| `TWILIO_*`, `PUBLIC_BASE_URL`, `ALLOWED_WHATSAPP_NUMBERS` | | WhatsApp, see above |
| `GROCERYBOT_DATA_DIR` | `data` | Database location |
| `BROWSER_PROFILE_DIR` | `data/browser-profile` | Logged-in browser profile |

# grocerybot architecture

```
 WhatsApp group / 1:1 chat            lo-fi web chat (now)
          │                                  │
   [channel adapter]  ──────────────►  POST /api/send
   Twilio webhook / WA bridge                │
          │                                  ▼
          └──────────────►  asyncio.Queue  (single worker = strict ordering)
                                     │
                                     ▼
                              Bot.handle_message(chat_id, sender, text) -> [replies]
                               │            │                 │
                         matcher.py      db.py (SQLite)     amazon.py
                   alias → fuzzy → LLM   products, aliases,  Mock | Playwright
                                         pending questions,  (persistent Chromium
                                         audit log            profile, logged in once)
                                     │
                                     ▼
                       reply via same channel ("✅ Added 1x … to basket")
```

The rule that keeps this simple: **`Bot` only sees text in and text out.** Channels (web chat,
Twilio, a group bridge) are thin adapters, so you can swap them freely.

## 1. Amazon side: browser automation with a persistent session

Amazon has no public Fresh/cart API, so the only route is driving a real browser.

- **Playwright `launch_persistent_context(data/browser-profile)`** keeps cookies on disk.
  You run `scripts/login.py` once (headed), log in with 2FA and tick *Keep me signed in*.
  After that the server runs headless and reuses the session. In practice that lasts weeks
  to months, but Amazon will sometimes ask for a password or OTP again.
- **Spotting an expired session:** any time we land on `/ap/signin` we raise `SessionExpired`
  and the bot posts "⚠️ session expired" to the chat. Add a daily cron that calls
  `is_logged_in()` so you find out before you need it.
- **Adding to the basket:** open `/dp/{ASIN}?almBrandId=<Fresh>`, click the Fresh add button, and
  check that `#nav-cart-count` went up. The ASIN is the stable key, so matching only has to
  produce an ASIN. **The selectors in `amazon.py` are best-effort.** Expect to adjust them on
  your first live run with `HEADLESS=0`.
- **One browser, one action at a time** (`asyncio.Lock` plus a single queue worker). This avoids
  races and keeps the traffic looking human. Random small delays are already built in.

### Where to host it

| Option | Pros | Cons |
|---|---|---|
| **Home box** (Mac mini / Pi / old laptop) + ngrok, Cloudflare Tunnel or Tailscale Funnel for the webhook | Residential IP: Amazon sees the same IP and device you shop from, so far fewer captchas and logouts | Needs a machine that's always on |
| VPS (Hetzner, etc.) | Clean, always on | Datacentre IPs are much more likely to get captchas and forced re-logins. Logging in needs a remote display: `ssh -X`, or Xvfb + x11vnc/noVNC |

**Recommendation:** start on a home machine. If you move to a VPS, do the login *on* the VPS
through noVNC rather than copying the profile over. A session that suddenly moves from home
to a datacentre IP is exactly what Amazon's risk checks look for.

Heads-up: automating amazon.com is against Amazon's Conditions of Use. At personal scale,
with a few adds a day on your own account, the realistic risk is captchas or forced logouts,
but the account at stake is yours.

## 2. Order history → product catalogue

`scripts/scrape_history.py 2024 2025 2026`:
1. Go through *Your Orders* by year and keep orders that look like Fresh orders.
2. Open **each order's detail page**. Large Fresh orders show only a few thumbnails on the list page.
3. Pull ASIN, title and qty from each line into `order_lines`, keyed by `(order_id, asin)`
   so re-runs are safe.
4. Work out per-product `times_ordered`, `total_qty`, `usual_qty` (the most common qty) and
   `last_ordered`.

`usual_qty` matters: "milk" adds the 2 you normally buy, while "1 milk" overrides it.
Fallback if the scraper fights you: Amazon's *Request My Data* export includes full order
history as CSV. Writing a CSV importer into the same table is about 20 lines.

## 3. Matching ("low fat milk" → ASIN)

The matcher tries the cheapest option first and only moves on when it isn't sure:

1. **Learned alias.** You confirmed "low fat milk" → B0xx once, so it goes straight through
   from then on. Each time you pick an option, the bot learns that alias.
2. **Fuzzy match** (rapidfuzz WRatio plus a small boost for frequently ordered items). If it's a
   clear winner (score ≥ 92 with a big gap to the next result), the item is added immediately.
3. **LLM pick** from the top 15 fuzzy candidates, including how often you order each one.
   This catches synonyms like "low fat" vs "semi-skimmed" and judges generic requests
   ("milk" → the one you buy most often). It returns structured JSON:
   `{asin, confident, quantity, alternatives}`. If `confident` is true, the item is added.
   If not, the bot asks "Which one? 1/2/3". The LLM can only choose ASINs from the list,
   so it can't invent products.
4. **Not in your history:** search Amazon Fresh and offer the top 3.

Questions are queued per chat and asked one at a time, so "milk, bananas, dragonfruit" with
two unclear items works. Reply with `1`–`4` or `none`.

## 4. Channels

### Lo-fi web chat (local only)
`static/chat.html` is a WhatsApp-styled page. It has a "send as" picker so you can act out two
family members. It posts to `/api/send` and polls `/api/messages`. Replies arrive
asynchronously, the same way real WhatsApp delivery behaves. It has no auth, so the server
only serves it to direct localhost requests; anything arriving through a tunnel gets a 404.

### WhatsApp via Twilio. ⚠️ Groups are the catch
- **Twilio's WhatsApp API doesn't support group chats.** It's 1:1 between a person and the
  business number, so each household member messages the bot number directly. They all
  fill the same Amazon basket, so in practice this works like a group.
- Inbound is `POST /twilio/whatsapp`, outbound is `twilio_channel.py`. Requests must carry a
  valid `X-Twilio-Signature` for `PUBLIC_BASE_URL`, and the sender must be in
  `ALLOWED_WHATSAPP_NUMBERS`; everything else is dropped.
- Start on the Twilio sandbox. For a dedicated number, register a WhatsApp sender through
  Twilio's Self Sign-up (needs a Meta Business Portfolio and Meta business verification),
  then change `TWILIO_WHATSAPP_FROM`.
- **If it must be a group:** put a spare SIM's number in the group and run an unofficial
  WhatsApp Web bridge such as **Baileys** (Node) or **whatsapp-web.js** linked to it. It forwards
  group messages to `/api/send` and posts replies back. It's free and works in groups, but
  it breaks WhatsApp's ToS and can get that number banned. With low volume on a spare
  number, that risk is acceptable for many people.
- Either way, it's one adapter file that calls the queue with a `reply` callback.

## 5. Roadmap
- [x] Mock end-to-end: chat UI → matcher → fake basket
- [x] Real Amazon via Playwright (login once, CSV import or history scrape)
- [x] Twilio WhatsApp 1:1 with signature check and sender allowlist
- [x] Raspberry Pi deployment in Docker (`scripts/deploy_pi.sh`)
- [ ] Daily session health check that posts a warning to the chat
- [ ] Live `undo` / `basket` (cart page scrape)
- [ ] Optional: Baileys group bridge
- [ ] Optional: "delivery slot booked / basket total" summaries

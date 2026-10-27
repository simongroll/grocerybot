"""Channel-agnostic brain: text in, replies out.

The web chat, Twilio and a WhatsApp group bridge all call `handle_message`;
none of them know anything about Amazon or matching.
"""

import re

from . import db, matcher
from .amazon import SessionExpired

HELP = (
    "Send item names, one per line or comma-separated, e.g.\n"
    "  low fat milk\n  2 bananas, eggs x2\n"
    "Other commands: *basket*, *undo*, *help*"
)
ASK_MORE = "Let me know how many more you'd like to add."


class Bot:
    def __init__(self, amazon):
        self.amazon = amazon

    async def handle_message(self, chat_id: str, sender: str, text: str) -> list[str]:
        text = text.strip()
        low = text.lower()
        if not text:
            return []
        if low in ("help", "?"):
            return [HELP]
        if low == "basket":
            lines = await self.amazon.basket_summary()
            return ["🧺 Basket:\n" + "\n".join(lines) if lines else "Basket summary isn't available (or it's empty)."]
        if low == "undo":
            return [await self._undo()]

        try:
            # Reply to a "which one?" question: "1", "2", ... or "0"/"none"
            if (re.fullmatch(r"\d", low) or low in ("none", "skip")) and (p := db.peek_pending(chat_id)):
                db.delete_pending(p["id"])
                n = int(low) if low.isdigit() else 0
                reply, added = await self._resolve(p, sender, n)
                return self._ask_more(chat_id, [reply], [added] if added else []) + self._next_question(chat_id)

            # Reply to "how many more?"; any other text drops the question and is handled as items
            more_asin = db.pop_followup(chat_id)
            if re.fullmatch(r"\d{1,2}", low):
                if not more_asin:
                    return ["There's no open question to answer — send an item name."]
                if int(low) == 0:
                    return ["👍 No more added."]
                reply, _ = await self._add(more_asin, int(low), sender, alias=None)
                return [reply]

            had_question = db.peek_pending(chat_id) is not None
            replies, added = [], []
            for item in [i for i in re.split(r"[\n,;]+", text) if i.strip()]:
                reply, asin = await self._handle_item(chat_id, sender, item)
                if reply:
                    replies.append(reply)
                if asin:
                    added.append(asin)
            # Ask at most one question at a time; the rest queue up behind it.
            replies = self._ask_more(chat_id, replies, added)
            return replies + ([] if had_question else self._next_question(chat_id))
        except SessionExpired:
            return ["⚠️ Amazon session has expired — someone needs to re-run the login script."]

    def _ask_more(self, chat_id: str, replies: list[str], added: list[str]) -> list[str]:
        """Offer "how many more?" when exactly one item was added and no "which one?" is
        waiting, so a bare number can't be read two ways."""
        if len(added) != 1 or db.peek_pending(chat_id):
            return replies
        db.set_followup(chat_id, added[0])
        return replies[:-1] + [f"{replies[-1]}\n{ASK_MORE}"]

    async def _handle_item(self, chat_id: str, sender: str, item: str) -> tuple[str | None, str | None]:
        m = matcher.match(item)
        if m.asin:
            return await self._add(m.asin, m.qty, sender, alias=m.phrase if m.reason != "alias" else None)

        if not m.options:
            # Nothing in history — offer Amazon search results instead
            m.options = await self.amazon.search(m.phrase, n=3)
            if not m.options:
                return f"🤷 Couldn't find anything for “{m.phrase}”.", None
            for o in m.options:
                db.upsert_product(o["asin"], o["title"])
            prefix = f"“{m.phrase}” isn't in your order history. Top Fresh results:"
        else:
            prefix = f"Which “{m.phrase}”?"

        db.add_pending(chat_id, m.phrase, m.qty or 0, m.options, prefix)
        return None, None

    def _next_question(self, chat_id: str) -> list[str]:
        p = db.peek_pending(chat_id)
        if not p:
            return []
        opts = "\n".join(f"{i + 1}. {self._option_label(o)}" for i, o in enumerate(p["options"]))
        return [f"{p['prompt']}\n{opts}\nReply with a number, or *none*."]

    @staticmethod
    def _option_label(o: dict) -> str:
        prod = db.get_product(o["asin"])
        return f"{o['title']} — {prod['price']}" if prod and prod["price"] else o["title"]

    async def _resolve(self, p: dict, sender: str, n: int) -> tuple[str, str | None]:
        if n == 0 or n > len(p["options"]):
            return f"👍 Skipped “{p['phrase']}”.", None
        choice = p["options"][n - 1]
        return await self._add(choice["asin"], p["qty"] or None, sender, alias=p["phrase"])

    async def _add(self, asin: str, qty: int | None, sender: str, alias: str | None) -> tuple[str, str | None]:
        """Returns the reply and, if it worked, the ASIN added."""
        prod = db.get_product(asin)
        qty = qty or (prod["usual_qty"] if prod else 1)
        res = await self.amazon.add_to_cart(
            (prod and prod["buy_asin"]) or asin, qty, expected_title=prod["title"] if prod else None
        )
        if not res.ok:
            return f"❌ Couldn't add {res.title or asin}: {res.error}", None
        if alias:
            db.set_alias(alias, asin)  # next time "milk" goes straight through
        db.log_cart_add(asin, res.title, qty, sender)
        return f"✅ Added {qty}x {res.title} to basket", asin

    async def _undo(self) -> str:
        last = db.last_cart_add()
        if not last:
            return "Nothing to undo."
        if await self.amazon.remove_from_cart(last["asin"], last["qty"]):
            db.delete_cart_add(last["id"])
            return f"↩️ Removed {last['qty']}x {last['title']}"
        return f"Undo isn't supported for the live basket yet — please remove {last['title']} manually."

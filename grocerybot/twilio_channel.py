"""WhatsApp via Twilio.

Inbound: Twilio POSTs form data to /twilio/whatsapp. We check the signature (so randoms
can't fill your basket), check the sender is on the allowlist, and queue the message.
Outbound: one REST call per reply. The webhook itself returns empty TwiML straight away,
because adding several items in the browser can take longer than Twilio's 15s timeout.
"""

import asyncio
import os

from twilio.request_validator import RequestValidator
from twilio.rest import Client

from . import db

_client: Client | None = None


def is_valid_request(path: str, form: dict, signature: str) -> bool:
    # Twilio signs the public URL it called, not the localhost URL behind your tunnel.
    base, token = os.getenv("PUBLIC_BASE_URL"), os.getenv("TWILIO_AUTH_TOKEN")
    if not base or not token:
        print("[twilio] PUBLIC_BASE_URL / TWILIO_AUTH_TOKEN not set; rejecting webhook")
        return False
    return RequestValidator(token).validate(base.rstrip("/") + path, form, signature)


def is_allowed_sender(whatsapp_from: str) -> bool:
    allowed = {n.strip() for n in os.getenv("ALLOWED_WHATSAPP_NUMBERS", "").split(",") if n.strip()}
    return whatsapp_from.removeprefix("whatsapp:") in allowed


async def reply_via_twilio(to: str, body: str):
    global _client
    _client = _client or Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    db.log_message(to, "bot", body)
    await asyncio.to_thread(
        _client.messages.create, from_=os.environ["TWILIO_WHATSAPP_FROM"], to=to, body=body
    )

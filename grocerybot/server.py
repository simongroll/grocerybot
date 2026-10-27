"""HTTP entrypoint.

  GET  /                  lo-fi WhatsApp-style chat for testing
  POST /api/send          chat UI posts a message
  GET  /api/messages      chat UI polls for new messages
  POST /twilio/whatsapp   Twilio webhook (v2 — see ARCHITECTURE.md)

Messages are processed in the background and replies are *pushed* into the
chat log, the same shape as WhatsApp/Twilio, where the webhook returns
immediately and the bot replies with a separate API call.
"""

import asyncio
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import config, db
from .amazon import make_client
from .bot import Bot

amazon = make_client()
bot = Bot(amazon)
queue: asyncio.Queue = asyncio.Queue()


async def worker():
    # Single consumer: messages are handled strictly in order, one browser action at a time.
    while True:
        chat_id, sender, text, reply = await queue.get()
        try:
            # One combined message per inbound: halves WhatsApp cost and respects the
            # Twilio sandbox's 1-message-per-3-seconds limit.
            if replies := await bot.handle_message(chat_id, sender, text):
                await reply(chat_id, "\n\n".join(replies))
        except Exception as e:
            traceback.print_exc()
            try:
                await reply(chat_id, f"💥 Something broke: {e}")
            except Exception:
                traceback.print_exc()  # the reply channel itself is down; keep the worker alive
        finally:
            queue.task_done()


async def reply_to_web(chat_id: str, body: str):
    db.log_message(chat_id, "bot", body)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    await amazon.start()
    task = asyncio.create_task(worker())
    print(f"[grocerybot] backend={config.AMAZON_BACKEND} llm={'on' if config.LLM_ENABLED else 'off'}")
    yield
    task.cancel()
    await amazon.stop()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def web_chat_is_local_only(request: Request, call_next):
    # A tunnel (ngrok/cloudflared) makes the whole app public, and the web chat has no auth.
    # Tunnels forward from localhost but add X-Forwarded-For, so that's what we check.
    if not request.url.path.startswith("/twilio/"):
        local = request.client and request.client.host in ("127.0.0.1", "::1")
        if not local or "x-forwarded-for" in request.headers:
            return Response(status_code=404)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(config.ROOT / "static" / "chat.html")


class Incoming(BaseModel):
    chat_id: str = "family"
    sender: str
    text: str


@app.post("/api/send")
async def send(msg: Incoming):
    mid = db.log_message(msg.chat_id, msg.sender, msg.text)
    await queue.put((msg.chat_id, msg.sender, msg.text, reply_to_web))
    return {"id": mid}


@app.get("/api/messages")
def messages(chat_id: str = "family", after: int = 0):
    return db.messages_after(chat_id, after)


@app.post("/twilio/whatsapp")
async def twilio_whatsapp(request: Request):
    """Twilio sends form-encoded webhooks. Return 200 fast; reply via REST API."""
    from . import twilio_channel

    form = dict(await request.form())
    if not twilio_channel.is_valid_request(request.url.path, form, request.headers.get("X-Twilio-Signature", "")):
        return Response(status_code=403)
    sender, body = form.get("From", ""), form.get("Body", "")
    if twilio_channel.is_allowed_sender(sender):
        name = form.get("ProfileName") or sender
        db.log_message(sender, name, body)
        await queue.put((sender, name, body, twilio_channel.reply_via_twilio))
    else:
        print(f"[twilio] ignoring message from non-allowlisted {sender}")
    return Response(content="<Response/>", media_type="application/xml")

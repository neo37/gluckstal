import hashlib
import hmac
import html
import json
import os
import time
import urllib.error
import urllib.request

from django.utils.translation import gettext as _

DEFAULT_API = "https://api.telegram.org"


def _opener():
    """Optional outbound proxy for Telegram (env TELEGRAM_PROXY=http://user:pass@host:port)."""
    proxy = os.environ.get("TELEGRAM_PROXY")
    handlers = [urllib.request.ProxyHandler({"https": proxy, "http": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def call(token, method, payload=None, base=""):
    url = f"{(base or DEFAULT_API).rstrip('/')}/bot{token}/{method}"
    req = urllib.request.Request(url, data=json.dumps(payload or {}).encode(), headers={"Content-Type": "application/json"})
    try:
        with _opener().open(req, timeout=10) as r:
            data = json.load(r)
            return data if isinstance(data, dict) else {"ok": False, "description": "unexpected response"}
    except urllib.error.HTTPError as e:  # Telegram puts the error description into the body
        try:
            return json.load(e)
        except ValueError:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except ValueError:  # not JSON (e.g. an HTML error page from a relay)
        return {"ok": False, "description": "unexpected response"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"ok": False, "description": _("нет связи с Telegram (%s)") % getattr(e, "reason", e)}


def send_all(settings, text):
    """Send to every configured chat. None — bot is not configured; otherwise {chat_id: 'ok' | error}."""
    chats = settings.chat_list()
    if not (settings.bot_token and chats):
        return None
    return {c: ("ok" if (r := call(settings.bot_token, "sendMessage", {
        "chat_id": c, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        base=settings.telegram_api_url)).get("ok") else r.get("description", "error")) for c in chats}


def get_me(settings):
    me = call(settings.bot_token, "getMe", base=settings.telegram_api_url)
    if not me.get("ok"):
        raise ValueError(_("Telegram не принял токен: %s") % me.get("description", ""))
    return me["result"]["username"]


def find_chats(settings):
    """The bot's username and the chats that wrote to it recently (getUpdates)."""
    username = get_me(settings)
    up = call(settings.bot_token, "getUpdates", {"timeout": 0}, base=settings.telegram_api_url)
    if not up.get("ok"):
        raise ValueError(up.get("description", "getUpdates failed"))
    chats = {}
    for u in up["result"]:
        for key in ("message", "my_chat_member", "channel_post", "edited_message"):
            c = (u.get(key) or {}).get("chat")
            if c:
                name = " ".join(filter(None, [c.get("first_name"), c.get("last_name")])) or c.get("title") or ""
                chats[c["id"]] = {"id": str(c["id"]), "type": c.get("type"), "name": name, "username": c.get("username") or ""}
    return username, list(chats.values())


def check_login(data, bot_token, max_age=86400):
    """Verify data from the Telegram Login Widget (https://core.telegram.org/widgets/login#checking-authorization).

    Works offline: only the bot token is needed, no request to Telegram.
    """
    data = {k: str(v) for k, v in data.items()}
    received = data.pop("hash", "")
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(expected, received):
        return None
    try:
        if time.time() - int(data.get("auth_date", 0)) > max_age:
            return None
        data["id"] = int(data["id"])
    except (KeyError, ValueError):
        return None
    return data


def format_order(d):
    e = lambda k: html.escape(d.get(k, "") or "—")
    lines = [
        "🧵 <b>" + _("Новая заявка с сайта GLÜCKSTAL") + "</b>", "",
        f"<b>{_('Имя')}:</b> {e('name')}",
        f"<b>{_('Контакт')}:</b> {e('contact')}",
        f"<b>{_('Связаться через')}:</b> {e('via')}",
        f"<b>{_('Изделие')}:</b> {e('product')}",
    ]
    if d.get("lang") == "en":
        lines.append(f"<b>{_('Язык сайта')}:</b> English 🇬🇧")
    lines += [f"<b>{_('Пожелания')}:</b>", e("comment")]
    return "\n".join(lines)

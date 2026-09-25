"""Own lightweight visit analytics: who came (IP, device), from where, how long they stayed, what they clicked."""
import datetime
import json
import re
from urllib.parse import parse_qs, urlparse

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Visit

BOT_RE = re.compile(r"bot|crawl|spider|slurp|headless|lighthouse|preview|curl|wget|python-|httpclient|monitor", re.I)
EVENTS = {"product_open", "order_click", "order_sent", "tg_click", "wa_click", "cta_header", "lang_switch"}
SESSION_GAP = datetime.timedelta(minutes=30)
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")


def client_ip(request):
    # nginx sets X-Forwarded-For to the real client address
    return (request.headers.get("X-Forwarded-For") or request.META.get("REMOTE_ADDR", "")).split(",")[0].strip() or None


def parse_ua(ua):
    """(device label, device type) from a User-Agent string, without external libraries."""
    if not ua:
        return "", ""
    if BOT_RE.search(ua):
        return "Bot", "bot"
    os_name = ("iOS" if re.search(r"iPhone|iPad|iPod", ua) else "Android" if "Android" in ua
               else "Windows" if "Windows" in ua else "macOS" if "Mac OS X" in ua else "Linux" if "Linux" in ua else "")
    m = re.search(r"(?:iPhone OS|CPU OS) (\d+)", ua) or re.search(r"Android (\d+)", ua)
    if m and os_name in ("iOS", "Android"):
        os_name += f" {m.group(1)}"
    browser = next((name for name, pat in (
        ("Yandex Browser", r"YaBrowser"), ("Edge", r"Edg/"), ("Opera", r"OPR/|Opera"), ("Samsung Internet", r"SamsungBrowser"),
        ("Telegram", r"Telegram"), ("VK", r"VKAndroidApp|com\.vk"), ("Firefox", r"Firefox|FxiOS"),
        ("Chrome", r"Chrome|CriOS"), ("Safari", r"Safari")) if re.search(pat, ua)), "")
    kind = "tablet" if re.search(r"iPad|Tablet", ua) else "mobile" if re.search(r"Mobi|iPhone|Android", ua) else "desktop"
    model = ""
    if "iPhone" in ua:
        model = "iPhone"
    elif "iPad" in ua:
        model = "iPad"
    else:
        m = re.search(r"Android [\d.]+; (?:[a-z]{2}-[a-z]{2}; )?([^;)]+?)(?: Build|\))", ua)
        if m and m.group(1).strip() not in ("K", "wv"):
            model = m.group(1).strip()[:40]
    label = " · ".join(x for x in (model, os_name, browser) if x)
    return label[:120], kind


def source_of(referrer, landing, own_host):
    q = parse_qs(urlparse(landing).query)
    if q.get("utm_source"):
        return q["utm_source"][0][:100]
    host = urlparse(referrer).hostname or ""
    if not host or host == own_host:
        return "direct"
    host = host.removeprefix("www.").removeprefix("m.").removeprefix("l.")
    for name, pat in (("google", "google."), ("yandex", "yandex."), ("vk", "vk.com"), ("telegram", "t.me"),
                      ("instagram", "instagram."), ("tilda", "tilda.ws")):
        if pat in host:
            return name
    return host[:100]


@csrf_exempt
@require_POST
def track(request):
    """Beacon endpoint: {v: visit key, u: visitor key, t: start|ping|event|end, e: event, a: active seconds, ...}."""
    try:
        d = json.loads(request.body[:4096] or b"{}")
        assert isinstance(d, dict)
    except (ValueError, AssertionError):
        return JsonResponse({"error": "bad json"}, status=400)
    key, visitor = str(d.get("v", "")), str(d.get("u", ""))
    if not (KEY_RE.match(key) and KEY_RE.match(visitor)):
        return JsonResponse({"error": "bad key"}, status=400)
    ua = request.headers.get("User-Agent", "")[:400]
    device, kind = parse_ua(ua)
    if kind == "bot":
        return HttpResponse(status=204)

    visit = Visit.objects.filter(key=key).first()
    if visit is None:
        if d.get("t") != "start":
            return HttpResponse(status=204)  # unknown visit (e.g. after cleanup) — ignore pings
        landing = str(d.get("url", ""))[:300]
        referrer = str(d.get("ref", ""))[:300]
        visit = Visit.objects.create(
            key=key, visitor=visitor, ip=client_ip(request), user_agent=ua, device=device, device_type=kind,
            referrer=referrer, landing=landing, source=source_of(referrer, landing, request.get_host().split(":")[0]),
            lang=str(d.get("lang", ""))[:5], screen=str(d.get("scr", ""))[:20],
            is_staff=bool(getattr(request, "user", None) and request.user.is_authenticated and request.user.is_staff))
        return HttpResponse(status=204)

    if timezone.now() - visit.last_seen > SESSION_GAP * 4:  # stale key reused much later — don't glue sessions
        return HttpResponse(status=204)
    active = min(int(d.get("a") or 0), 6 * 3600)
    visit.active_seconds = max(visit.active_seconds, active)
    if d.get("t") == "start":
        visit.pageviews += 1
    if d.get("t") == "event" and d.get("e") in EVENTS:
        visit.events[d["e"]] = visit.events.get(d["e"], 0) + 1
    visit.save(update_fields=["active_seconds", "pageviews", "events", "last_seen"])
    return HttpResponse(status=204)

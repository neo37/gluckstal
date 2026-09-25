import datetime
import json
import logging
import re
import threading
import time
from collections import defaultdict, deque

from django.conf import settings
from django.core.cache import cache
from django.db.models import Prefetch
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import telegram
from .images import variant_url
from .analytics import client_ip
from .models import Category, Faq, InfoCard, Order, Product, ProductImage, Review, SiteSettings, Visit, page_cache_key

log = logging.getLogger(__name__)


def lang():
    return "en" if (get_language() or "").startswith("en") else "ru"


def money(value):
    """6 500 ₽ (ru) / 6,500 ₽ (en)."""
    n = f"{value:,}".replace(",", " " if lang() == "ru" else ",")
    return f"{n} ₽"


def price_text(p):
    return (_("от") + " " if p.price_from else "") + money(p.price)


def paragraphs(text):
    """Paragraphs separated by a blank line, **bold** — for admin-edited texts (everything else is escaped)."""
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        if block.strip():
            b = escape(block.strip()).replace("\n", "<br>")
            out.append("<p>" + re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", b) + "</p>")
    return mark_safe("\n".join(out))


def page_url(site, code):
    return f"{site}/" if code == settings.LANGUAGE_CODE else f"{site}/{code}/"


def _context():
    s = SiteSettings.load()
    code = lang()
    products = list(Product.objects.filter(visible=True).select_related("category")
                    .prefetch_related(Prefetch("images", queryset=ProductImage.objects.order_by("order", "id"))))
    for p in products:
        p.imgs = list(p.images.all())
        p.cover = variant_url(p.imgs[0].image, 600) if p.imgs else ""
        p.label = p.tr("name")
        p.points = p.bullets()
        p.price_label = price_text(p)
    cats = []
    for c in Category.objects.all():
        c.n = sum(p.category_id == c.id for p in products)
        c.label = c.tr("name")
        if c.n:
            cats.append(c)
    min_price = min((p.price for p in products), default=0)
    site = s.site_url.rstrip("/")
    here = page_url(site, code)
    og_image = (site + s.hero_image.url.rsplit("/", 1)[0] + "/og.jpg") if s.hero_image else ""

    t = {  # strings used by app.js
        "from": _("от"), "photo": _("фото"), "of": _("из"), "numLocale": "en-US" if code == "en" else "ru-RU",
        "fill": _("Заполните имя, контакт и подтвердите согласие."), "sending": _("Отправляю…"),
        "sent": _("Спасибо! Заявка отправлена — скоро свяжусь с вами."),
        "failed": _("Не получилось отправить. Напишите, пожалуйста, напрямую в Telegram или позвоните."),
        "photoN": _("Фото"), "details": _("фото и описание"),
    }
    data = {
        "lang": code, "t": t,
        "cats": {c.slug: c.label for c in cats},
        "tags": {"him": [p.slug for p in products if p.for_him], "her": [p.slug for p in products if p.for_her]},
        "tg": s.telegram, "phone": s.phone_raw, "phoneText": s.phone,
        "products": [{"slug": p.slug, "name": p.label, "cat": p.category.slug, "price": p.price, "priceFrom": p.price_from,
                      "desc": p.points, "imgs": [[variant_url(i.image, 600), variant_url(i.image, 1200)] for i in p.imgs]}
                     for p in products],
    }
    jsonld = {"@context": "https://schema.org", "@graph": [
        {"@type": "Store", "@id": site + "/#store", "name": s.brand, "url": here, "inLanguage": code,
         "description": s.tr("seo_description"), "telephone": s.phone_raw, "email": s.email, "image": og_image,
         "address": {"@type": "PostalAddress", "addressLocality": s.tr("city"), "addressCountry": "RU"},
         "founder": {"@type": "Person", "name": s.tr("master_name")},
         "sameAs": [u for u in (s.vk_url, f"https://t.me/{s.telegram}" if s.telegram else "") if u]},
        {"@type": "ItemList", "name": _("Каталог изделий") + " " + s.brand, "itemListElement": [
            {"@type": "ListItem", "position": n, "item": {
                "@type": "Product", "name": p.label, "description": " ".join(p.points), "url": f"{here}#p={p.slug}",
                "image": site + variant_url(p.imgs[0].image, 1200) if p.imgs else "",
                "brand": {"@type": "Brand", "name": s.brand}, "material": _("натуральная кожа"),
                "offers": {"@type": "Offer", "price": p.price, "priceCurrency": "RUB",
                           "availability": "https://schema.org/MadeToOrder", "seller": {"@id": site + "/#store"}}}}
            for n, p in enumerate(products, 1)]},
    ]}

    def js(obj):
        return mark_safe(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))

    reviews = list(Review.objects.filter(visible=True))
    for r in reviews:
        r.photo_url = variant_url(r.photo, 600) if r.photo else ""
    perks, steps = [], []
    for card in InfoCard.objects.all():
        (perks if card.kind == "perk" else steps).append(card)
    return {
        "s": s, "site": site, "lang": code, "here": here,
        "alternates": [(c, page_url(site, c)) for c, _n in settings.LANGUAGES],
        "other_lang": "ru" if code == "en" else "en", "other_url": "/" if code == "en" else "/en/",
        "products": products, "cats": cats, "count": len(products), "min_price": money(min_price),
        "under2000": sum(p.price <= 2000 for p in products), "under2000_label": money(2000),
        "perks": perks, "steps": steps, "faqs": Faq.objects.filter(visible=True), "reviews": reviews,
        "about_html": paragraphs(s.tr("about_text")), "custom_html": paragraphs(s.tr("custom_text")),
        "hero_900": variant_url(s.hero_image, 900), "hero_1600": variant_url(s.hero_image, 1600), "og_image": og_image,
        "about_img": variant_url(s.about_photo, 900), "workshop_img": variant_url(s.workshop_photo, 900),
        "custom_img": variant_url(s.custom_photo, 900), "logo_img": variant_url(s.logo, 240),
        "privacy_url": "/en/privacy.html" if code == "en" else "/privacy.html",
        "year": datetime.date.today().year, "data_json": js(data), "jsonld": js(jsonld),
    }


def index(request):
    key = page_cache_key(lang())
    body = cache.get(key)
    if body is None:
        body = render_to_string("shop/index.html", _context(), request=None)
        cache.set(key, body, 3600)
    return HttpResponse(body)


def privacy(request):
    return render(request, "shop/privacy.html", {"s": SiteSettings.load(), "date": datetime.date.today(), "lang": lang(),
                                                 "home": "/en/" if lang() == "en" else "/"})


def robots(request):
    site = SiteSettings.load().site_url.rstrip("/")
    return HttpResponse(f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin/\nDisallow: /i18n/\n\n"
                        f"Sitemap: {site}/sitemap.xml\n", content_type="text/plain")


def sitemap(request):
    site = SiteSettings.load().site_url.rstrip("/")
    alt = "".join(f'<xhtml:link rel="alternate" hreflang="{c}" href="{page_url(site, c)}"/>' for c, _n in settings.LANGUAGES)
    urls = "".join(f"  <url><loc>{page_url(site, c)}</loc><lastmod>{datetime.date.today()}</lastmod>{alt}</url>\n"
                   for c, _n in settings.LANGUAGES)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
           'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + urls + "</urlset>\n")
    return HttpResponse(xml, content_type="application/xml")


def health(request):
    s = SiteSettings.load()
    return JsonResponse({"ok": True, "telegram": bool(s.bot_token and s.chat_list())})


# --- order form -> Telegram
RATE_WINDOW, RATE_MAX, MIN_FILL_MS = 600, 5, 3000
LIMITS = {"name": 80, "contact": 120, "via": 20, "product": 120, "comment": 2000, "lang": 5}
_hits, _lock = defaultdict(deque), threading.Lock()


def _rate_ok(ip):
    now = time.time()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_MAX:
            return False
        q.append(now)
        return True


@csrf_exempt  # public form without a session; protected by traps, per-IP limit and Origin check
@require_POST
def order(request):
    origin = request.headers.get("Origin")
    if origin and origin.split("://", 1)[-1] != request.get_host():
        return JsonResponse({"error": "bad origin"}, status=403)
    try:
        d = json.loads(request.body[:16384] or b"{}")
        assert isinstance(d, dict)
    except (ValueError, AssertionError):
        return JsonResponse({"error": "bad json"}, status=400)
    if d.get("website") or int(d.get("elapsed") or 0) < MIN_FILL_MS:
        return JsonResponse({"ok": True})  # a bot: pretend everything is fine
    clean = {k: str(d.get(k) or "").strip()[:lim] for k, lim in LIMITS.items()}
    if len(clean["name"]) < 2 or len(clean["contact"]) < 2 or not d.get("consent"):
        return JsonResponse({"error": "name, contact and consent are required"}, status=422)
    ip = client_ip(request)
    if not _rate_ok(ip):
        return JsonResponse({"error": "too many requests"}, status=429)
    visit = Visit.objects.filter(key=str(d.get("visit") or "")[:40]).first() if d.get("visit") else None
    order = Order.objects.create(ip=ip, visit=visit, **clean)  # saved first: the order is never lost
    with translation.override(settings.LANGUAGE_CODE):  # the message is for the owner — in the site's main language
        res = telegram.send_all(SiteSettings.load(), telegram.format_order(clean))
    if res is None:
        order.telegram = "off"
    elif "ok" in res.values():
        order.telegram = "sent"
        order.telegram_error = "; ".join(f"{c}: {r}" for c, r in res.items() if r != "ok")[:300]
    else:
        order.telegram = "failed"
        order.telegram_error = "; ".join(f"{c}: {r}" for c, r in res.items())[:300]
        log.error("order %s saved, Telegram failed: %s", order.pk, res)
    order.save(update_fields=["telegram", "telegram_error"])
    return JsonResponse({"ok": True, "id": order.pk})

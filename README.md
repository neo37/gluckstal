# GLÜCKSTAL — website of a handmade leather workshop

**Live site: [gluckstal.store](https://gluckstal.store)** · English version: [gluckstal.store/en/](https://gluckstal.store/en/) · Project page: [neo37.github.io/gluckstal](https://neo37.github.io/gluckstal/)

GLÜCKSTAL is a small leather workshop in Novosibirsk: wallets, card holders, document holders, bags and belts, all hand-stitched to order.
This repository is its website: a fast one-page catalog with a product gallery, an order form that delivers orders to Telegram, a bilingual (Russian / English) interface and an admin panel where the owner manages **everything** shown on the site.

The repository contains **code only**. Product photos, texts, prices, contacts, the database and secrets are not stored here: they live in the site database and are managed from the admin panel (or loaded once with `import_content`).

## Features

**Public site**
- One-page catalog: categories, “under 2,000 ₽” and gift filters (for him / for her), product cards with a swipeable photo gallery and deep links (`/#p=<slug>`).
- Order form → saved to the database and sent to one or more Telegram chats. Spam protection: honeypot field, minimum fill time, per-IP rate limit, Origin check.
- Russian at `/`, English at `/en/` (hreflang, per-language canonical URLs, sitemap with alternates). Every content field has an English twin; if it is empty, the Russian text is shown.
- SEO: editable title/description, Open Graph preview image generated from the main photo, schema.org `Store` + `Product`/`Offer` markup, `robots.txt`, `sitemap.xml`.
- Photos uploaded in the admin are converted to responsive WebP variants automatically.
- Privacy policy page (Russian Federal Law 152-FZ), consent checkbox in the form.
- Optional Yandex Metrica with goals (`order_sent`, `order_click`, `tg_click`, `wa_click`, `product_open`, `cta_header`).

**Admin panel** (`/admin/`, Django admin, Russian and English UI)
- **Dashboard**: visits, unique visitors, average time on site, orders, conversion, clicks to Telegram/WhatsApp, a 30-day chart, traffic sources and devices, latest orders and visits (IP, device, time on site, events). Admin visits are excluded.
- **Content**: site settings (contacts, hero, “About me”, “Custom design” block, SEO, images), products with photo galleries, categories, reviews with photos, FAQ, benefit cards and ordering steps — each with an English version.
- **Orders**: every order with its status (new / in progress / done / cancelled), notes and Telegram delivery status.
- **Telegram bot**: token (write-only), “Find chats” (the bot lists who wrote to it — one click to add), “Send a test”, optional Bot API relay URL for servers where Telegram is blocked.
- **Log in with Telegram**: link an admin account to Telegram once, then log in with the Telegram Login Widget.
- **Backup**: one-click export of the whole site (SQLite database + photos) as a zip, and import with integrity/structure checks, zip-slip protection and an automatic backup of the current state before replacing it.

## Stack

- Python 3.12, Django 5.2, SQLite (WAL), Pillow, Gunicorn, WhiteNoise
- Vanilla JS and CSS on the public site (no build step), fonts from Google Fonts
- Docker / Docker Compose; nginx + Let's Encrypt in front

```
gluckstal/        Django project (settings, urls)
shop/             the app: models, admin, views, analytics, backup, telegram, templates, static, tests
locale/en/        English translation of the interface
docs/             GitHub Pages project page
Dockerfile, docker-compose.yml, entrypoint.sh, .env.example
```

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATA_DIR=./data ADMIN_USERNAME=admin ADMIN_PASSWORD='choose-a-long-one' DJANGO_DEBUG=1
python manage.py migrate
python manage.py ensure_admin
python manage.py compilemessages -l en
python manage.py runserver
```

Open http://127.0.0.1:8000/ (site) and http://127.0.0.1:8000/admin/ (admin). The site is empty until content is added in the admin or imported:

```bash
python manage.py import_content /path/to/content                  # content.json + img/ (format: see the command's docstring)
python manage.py import_content /path/to/content --translations   # only fill English fields of existing rows
```

## Deploy (Docker)

```bash
cp .env.example .env          # ADMIN_USERNAME / ADMIN_PASSWORD, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, COOKIE_SECURE=1
docker compose up -d --build  # listens on :8091, data in the "data" volume (/data)
```

Put nginx (or any reverse proxy) with HTTPS in front and pass `Host`, `X-Forwarded-For` and `X-Forwarded-Proto`:

```nginx
location / {
    proxy_pass http://127.0.0.1:8091;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 200m;   # backup imports
}
```

Everything that changes at runtime lives in `/data`: `db.sqlite3`, `media/` (uploaded photos), `backups/`, `cache/`, `django-secret.key`. Back up this volume (or use the admin export).

### Telegram setup
1. Create a bot with [@BotFather](https://t.me/BotFather) and paste the token in **Site settings → Orders to Telegram**.
2. The person who should receive orders opens the bot and presses **Start** (a bot cannot write first).
3. Click **Find chats → Add → Save → Send a test**.
4. For “Log in with Telegram”: run `/setdomain` in @BotFather for the site domain, then link your account on the admin home page.

If the server cannot reach `api.telegram.org` (it is blocked in some regions), set **Telegram Bot API URL** to a relay (any reverse proxy to `https://api.telegram.org`) or set the `TELEGRAM_PROXY` environment variable (`http://user:pass@host:port`). Orders are always saved in the database, so nothing is lost while Telegram is unreachable.

## Tests

```bash
DATA_DIR=$(mktemp -d) python manage.py test shop
```

The suite covers: every text/contact/SEO setting and every image upload reaching the page; adding, editing, hiding, deleting products and single photos through the real admin forms; reviews, FAQ and blocks; HTML escaping; the order form (Telegram delivery, traps, rate limit, Origin, failures); bot tools (token validation, find chats, test message); Telegram API errors; backup export/import round trip and rejection of bad archives; access control; the content import command.

## Security notes

- The bot token is never rendered back in the admin; the database lives in the `/data` volume only.
- Admin cookies are `Secure` + `HttpOnly` behind HTTPS (`COOKIE_SECURE=1`), CSRF and clickjacking protection.
- Backup import accepts only this site's SQLite schema, checks integrity, blocks path traversal and keeps the last 5 pre-import snapshots.

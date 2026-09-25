"""Tests: everything shown on the site is manageable from the admin (incl. uploads),
the Telegram bot integration, the order form, backup import/export and access control.

Run:  DATA_DIR=$(mktemp -d) python manage.py test shop
"""
import io
import re
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from PIL import Image

from . import views
from .images import variant_path
from .models import Category, Faq, InfoCard, Order, Product, ProductImage, Review, SiteSettings, TelegramAccount, Visit

TOKEN = "1234567890:AAFakeFakeFakeFakeFakeFakeFakeFake12"


def png(name="photo.png", size=(800, 600), color="#c69c67"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class MediaTmpMixin:
    """Every test gets its own empty MEDIA_ROOT and backups dir."""

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="gl-test-"))
        self._override = override_settings(MEDIA_ROOT=self._tmp / "media", DATA_DIR=self._tmp)
        self._override.enable()
        cache.clear()
        views._hits.clear()

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()


class AdminMixin(MediaTmpMixin):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("owner", password="test-only-password-123")
        self.client = Client()
        self.client.force_login(self.admin)

    # --- helpers that go through the real admin forms
    def settings_form(self, **changes):
        s = SiteSettings.load()
        data = {f.name: getattr(s, f.name) for f in SiteSettings._meta.fields
                if f.name not in ("id", "bot_token") and not f.name.endswith(("image", "photo", "logo"))}
        data = {k: ("" if v is None else v) for k, v in data.items()}
        data.update(bot_token="")
        data.update(changes)
        return data

    def post_settings(self, **changes):
        return self.client.post(reverse("admin:shop_sitesettings_change", args=[1]), self.settings_form(**changes))

    def add_product(self, images=(), **fields):
        cat = Category.objects.first() or Category.objects.create(name="Кошельки", slug="wallets")
        data = {"name": "Бумажник", "slug": "bumazhnik", "category": cat.pk, "price": "6500", "description": "Пункт один\nПункт два",
                "visible": "on", "order": "0",
                "images-TOTAL_FORMS": str(len(images)), "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000"}
        for i, img in enumerate(images):
            data[f"images-{i}-image"] = img
            data[f"images-{i}-order"] = str(i)
        data.update(fields)
        return self.client.post(reverse("admin:shop_product_add"), data)

    def page(self):
        cache.clear()
        return Client().get("/").content.decode()


# =============================================================== content management
class SiteContentFromAdminTests(AdminMixin, TestCase):
    def test_every_text_setting_appears_on_site(self):
        r = self.post_settings(
            brand="GLÜCKSTAL", master_name="Кира Тест", city="Новосибирск", phone="+7 (913) 000-11-22",
            email="kira@example.com", telegram="kira_test", vk_url="https://vk.com/test_gl",
            whatsapp="+7 913 000-11-22", old_site_url="https://gluckstal.tilda.ws/gluckstal",
            site_url="https://gluckstal.store", seo_title="SEO-заголовок теста", seo_description="SEO-описание теста",
            hero_eyebrow="Надзаголовок теста", hero_title="Заголовок теста", hero_lead="Подзаголовок теста",
            about_title="Меня зовут Кира Тест", about_text="Первый абзац **жирно**.\n\nВторой абзац.",
            custom_title="Своя модель теста", custom_text="Текст своей модели", metrika_id="98765432")
        self.assertEqual(r.status_code, 302, r.content[:2000])
        html = self.page()
        for text in ("Кира Тест", "Новосибирск", "+7 (913) 000-11-22", 'href="tel:+79130001122"', "kira@example.com",
                     "https://t.me/kira_test", "https://vk.com/test_gl", "https://wa.me/79130001122",
                     "https://gluckstal.tilda.ws/gluckstal", "Старый сайт (Tilda)", "<title>SEO-заголовок теста</title>",
                     'content="SEO-описание теста"', "Надзаголовок теста", "Заголовок теста", "Подзаголовок теста",
                     "Меня зовут Кира Тест", "<p>Первый абзац <b>жирно</b>.</p>", "<p>Второй абзац.</p>",
                     "Своя модель теста", "Текст своей модели", "window.__glMetrika=98765432",
                     'href="https://gluckstal.store/"'):
            self.assertIn(text, html, text)

    def test_optional_blocks_disappear_when_empty(self):
        self.post_settings(whatsapp="", old_site_url="", metrika_id="", telegram="", custom_title="")
        html = self.page()
        for text in ("wa.me", "Старый сайт", "mc.yandex.ru", "t.me/", 'class="custom"'):
            self.assertNotIn(text, html, text)

    def test_about_text_is_escaped(self):
        self.post_settings(about_text="<script>alert(1)</script> **ok**")
        html = self.page()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; <b>ok</b>", html)

    def test_settings_image_uploads(self):
        url = reverse("admin:shop_sitesettings_change", args=[1])
        data = self.settings_form(hero_title="X", custom_title="Своя модель")
        data.update(hero_image=png("hero.png", (1800, 1200)), logo=png("logo.png", (500, 500)),
                    about_photo=png("me.png", (900, 1300)), workshop_photo=png("ws.png"), custom_photo=png("c.png"))
        r = self.client.post(url, data)
        self.assertEqual(r.status_code, 302, r.content[:2000])
        s = SiteSettings.load()
        for field, widths in SiteSettings.IMAGE_VARIANTS.items():
            f = getattr(s, field)
            self.assertTrue(f, field)
            for w in widths:
                self.assertTrue(variant_path(f.path, w).exists(), f"{field}-{w}")
        self.assertTrue((Path(s.hero_image.path).parent / "og.jpg").exists())
        html = self.page()
        for field, widths in SiteSettings.IMAGE_VARIANTS.items():
            self.assertIn(getattr(s, field).url.rsplit(".", 1)[0] + f"-{widths[0]}.webp", html, field)
        self.assertIn('property="og:image"', html)
        # uploaded files are served
        r = Client().get(s.hero_image.url.rsplit(".", 1)[0] + "-900.webp")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/webp")

    def test_non_image_upload_rejected(self):
        data = self.settings_form()
        data["hero_image"] = SimpleUploadedFile("evil.png", b"<?php echo 1; ?>", content_type="image/png")
        r = self.client.post(reverse("admin:shop_sitesettings_change", args=[1]), data)
        self.assertEqual(r.status_code, 200)  # form re-rendered with an error
        self.assertFalse(SiteSettings.load().hero_image)


class CatalogFromAdminTests(AdminMixin, TestCase):
    def test_add_product_with_photos_through_admin(self):
        r = self.add_product(images=[png("a.png"), png("b.png", color="#333")], price_from="on", for_him="on")
        self.assertEqual(r.status_code, 302, r.content[:3000])
        p = Product.objects.get(slug="bumazhnik")
        self.assertEqual(p.images.count(), 2)
        for img in p.images.all():
            for w in (600, 1200):
                self.assertTrue(variant_path(img.image.path, w).exists())
        html = self.page()
        self.assertIn('data-slug="bumazhnik"', html)
        self.assertIn("от 6\u00a0500\u00a0₽", html)
        self.assertIn(p.images.first().image.url.rsplit(".", 1)[0] + "-600.webp", html)
        data = json.loads(html.split('<script id="data" type="application/json">')[1].split("</script>")[0])
        prod = data["products"][0]
        self.assertEqual(prod["desc"], ["Пункт один", "Пункт два"])
        self.assertEqual(len(prod["imgs"]), 2)
        self.assertIn("bumazhnik", data["tags"]["him"])
        self.assertIn("<option>Бумажник</option>", html)  # order form select

    def test_edit_hide_reorder_delete(self):
        self.add_product(images=[png()])
        p = Product.objects.get()
        # price change from the list page (list_editable)
        data = {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1", "form-0-id": p.pk, "form-0-price": "7000",
                "form-0-visible": "on", "form-0-order": "5", "_save": "Сохранить"}
        r = self.client.post(reverse("admin:shop_product_changelist"), data)
        self.assertEqual(r.status_code, 302)
        self.assertIn("7\u00a0000\u00a0₽", self.page())
        # hide
        data["form-0-visible"] = ""
        del data["form-0-visible"]
        self.client.post(reverse("admin:shop_product_changelist"), data)
        self.assertNotIn('data-slug="bumazhnik"', self.page())
        # delete product -> photos removed from disk
        img_path = Path(p.images.get().image.path)
        r = self.client.post(reverse("admin:shop_product_delete", args=[p.pk]), {"post": "yes"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(img_path.exists())
        self.assertFalse(variant_path(img_path, 600).exists())

    def test_delete_single_photo_through_inline(self):
        self.add_product(images=[png("a.png"), png("b.png")])
        p = Product.objects.get()
        first, second = p.images.all()
        data = {"name": p.name, "slug": p.slug, "category": p.category_id, "price": p.price, "description": p.description,
                "visible": "on", "order": 0, "images-TOTAL_FORMS": "2", "images-INITIAL_FORMS": "2",
                "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000",
                "images-0-id": first.pk, "images-0-product": p.pk, "images-0-order": 0, "images-0-DELETE": "on",
                "images-1-id": second.pk, "images-1-product": p.pk, "images-1-order": 1}
        r = self.client.post(reverse("admin:shop_product_change", args=[p.pk]), data)
        errors = r.context and [r.context["adminform"].form.errors, [f.errors for f in r.context["inline_admin_formsets"][0].formset]]
        self.assertEqual(r.status_code, 302, errors)
        self.assertEqual(list(p.images.values_list("pk", flat=True)), [second.pk])
        self.assertFalse(Path(first.image.path).exists())

    def test_categories_and_gift_filters(self):
        c1 = Category.objects.create(name="Сумки", slug="bags", order=1)
        Category.objects.create(name="Пустая", slug="empty", order=2)
        self.add_product(images=[png()], category=c1.pk, price="1500", for_her="on")
        html = self.page()
        self.assertIn('data-filter="bags"', html)
        self.assertNotIn('data-filter="empty"', html)  # categories without visible products are hidden
        self.assertIn('data-filter="under2000"', html)

    def test_reviews_faq_blocks(self):
        r = self.client.post(reverse("admin:shop_review_add"), {
            "author": "Анна", "product_name": "Бумажник", "text": "Отлично!\nВторая строка", "photo": png("r.png"),
            "link": "https://vk.com/wall1", "visible": "on", "order": 0})
        self.assertEqual(r.status_code, 302, r.content[:2000])
        self.client.post(reverse("admin:shop_review_add"), {"author": "Скрытый", "text": "не показывать", "order": 1})
        self.client.post(reverse("admin:shop_faq_add"), {"question": "Сколько шьёте?", "answer": "Неделю", "visible": "on", "order": 0})
        self.client.post(reverse("admin:shop_infocard_add"), {"kind": "perk", "title": "Плюс теста", "text": "Текст плюса", "order": 0})
        self.client.post(reverse("admin:shop_infocard_add"), {"kind": "step", "title": "Шаг теста", "text": "Текст шага", "order": 0})
        html = self.page()
        for text in ("Анна", "Отлично!<br>Вторая строка", "https://vk.com/wall1", "Сколько шьёте?", "Неделю",
                     "Плюс теста", "Текст плюса", "Шаг теста", "Текст шага"):
            self.assertIn(text, html, text)
        self.assertIn(Review.objects.get(author="Анна").photo.url.rsplit(".", 1)[0] + "-600.webp", html)
        self.assertNotIn("Скрытый", html)
        self.assertNotIn("reviews-cta", html)

    def test_reviews_placeholder_when_none(self):
        self.post_settings(vk_url="https://vk.com/gl")
        self.assertIn("reviews-cta", self.page())


# =============================================================== telegram bot
class BotSettingsTests(AdminMixin, TestCase):
    def test_token_validation_keep_and_clear(self):
        r = self.post_settings(bot_token="wrong")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(SiteSettings.load().bot_token, "")
        self.post_settings(bot_token=TOKEN, chat_ids="111, 222")
        self.assertEqual(SiteSettings.load().bot_token, TOKEN)
        self.assertEqual(SiteSettings.load().chat_list(), ["111", "222"])
        self.post_settings()  # blank field keeps the saved token
        self.assertEqual(SiteSettings.load().bot_token, TOKEN)
        page = self.client.get(reverse("admin:shop_sitesettings_change", args=[1])).content.decode()
        self.assertNotIn(TOKEN, page)  # never rendered back
        self.assertIn("1234567890:…ke12", page)
        self.post_settings(clear_token="on")
        self.assertEqual(SiteSettings.load().bot_token, "")

    def test_bad_chat_ids(self):
        self.assertEqual(self.post_settings(chat_ids="abc").status_code, 200)
        self.assertEqual(SiteSettings.load().chat_ids, "")

    @mock.patch("shop.telegram.call")
    def test_find_chats_and_test_message(self, call):
        self.post_settings(bot_token=TOKEN)

        def fake(token, method, payload=None, base=""):
            if method == "getMe":
                return {"ok": True, "result": {"username": "gl_bot"}}
            if method == "getUpdates":
                return {"ok": True, "result": [{"message": {"chat": {"id": 555, "type": "private", "first_name": "Кира",
                                                                     "username": "kira"}}}]}
            if method == "sendMessage":
                return {"ok": payload["chat_id"] == "555", "description": "Forbidden: bot was blocked"}
        call.side_effect = fake
        r = self.client.get(reverse("admin:shop_bot_chats")).json()
        self.assertEqual(r, {"bot": "gl_bot", "chats": [{"id": "555", "type": "private", "name": "Кира", "username": "kira"}]})
        r = self.client.post(reverse("admin:shop_bot_test"))
        self.assertEqual(r.status_code, 422)  # no chats yet
        self.post_settings(chat_ids="555, 666")
        r = self.client.post(reverse("admin:shop_bot_test")).json()
        self.assertEqual(r["results"], {"555": "ok", "666": "Forbidden: bot was blocked"})

    @mock.patch("shop.telegram.call", return_value={"ok": False, "description": "Unauthorized"})
    def test_bad_token_reported(self, call):  # noqa: D102
        self.post_settings(bot_token=TOKEN)
        r = self.client.get(reverse("admin:shop_bot_chats"))
        self.assertEqual(r.status_code, 422)
        self.assertIn("Unauthorized", r.json()["error"])


@override_settings(ALLOWED_HOSTS=["testserver"])
class OrderFormTests(MediaTmpMixin, TestCase):
    def setUp(self):
        super().setUp()
        s = SiteSettings.load()
        s.bot_token, s.chat_ids = TOKEN, "111, 222"
        s.save()
        self.c = Client()

    def post(self, headers=None, **d):
        body = {"name": "Анна <b>", "contact": "@anna", "via": "Telegram", "product": "Бумажник",
                "comment": "рыжая кожа", "consent": "on", "elapsed": 9000, **d}
        return self.c.post("/api/order", json.dumps(body), content_type="application/json", headers=headers or {})

    @mock.patch("shop.telegram.call", return_value={"ok": True})
    def test_order_sent_to_every_chat_escaped(self, call):
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c.args[2]["chat_id"] for c in call.call_args_list], ["111", "222"])
        text = call.call_args_list[0].args[2]["text"]
        self.assertIn("Анна &lt;b&gt;", text)
        self.assertIn("Бумажник", text)
        self.assertEqual(call.call_args_list[0].args[2]["parse_mode"], "HTML")

    @mock.patch("shop.telegram.call", return_value={"ok": True})
    def test_bots_and_invalid_requests(self, call):
        self.assertEqual(self.post(website="spam").json(), {"ok": True})
        self.assertEqual(self.post(elapsed=500).json(), {"ok": True})
        self.assertEqual(call.call_count, 0)  # traps: nothing sent
        self.assertEqual(self.post(consent="").status_code, 422)
        self.assertEqual(self.post(name="").status_code, 422)
        self.assertEqual(self.c.post("/api/order", "not json", content_type="application/json").status_code, 400)
        self.assertEqual(self.post(headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.c.get("/api/order").status_code, 405)

    @mock.patch("shop.telegram.call", return_value={"ok": True})
    def test_rate_limit(self, call):
        codes = [self.post().status_code for _ in range(6)]
        self.assertEqual(codes, [200] * 5 + [429])

    @mock.patch("shop.telegram.call", return_value={"ok": False, "description": "Bad Request: chat not found"})
    def test_telegram_failure_still_saves_order(self, call):
        r = self.post()
        self.assertEqual(r.status_code, 200)  # the visitor sees success: the order is stored
        o = Order.objects.get(pk=r.json()["id"])
        self.assertEqual(o.telegram, "failed")
        self.assertIn("chat not found", o.telegram_error)

    def test_not_configured_saves_order(self):
        s = SiteSettings.load()
        s.chat_ids = ""
        s.save()
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Order.objects.get().telegram, "off")
        self.assertFalse(self.c.get("/api/health").json()["telegram"])

    @mock.patch("shop.telegram.call", return_value={"ok": True})
    def test_order_saved_with_visit_and_language(self, call):
        Visit.objects.create(key="visitkey123", visitor="visitor123")
        r = self.post(visit="visitkey123", lang="en")
        o = Order.objects.get(pk=r.json()["id"])
        self.assertEqual((o.name, o.contact, o.product, o.lang, o.telegram), ("Анна <b>", "@anna", "Бумажник", "en", "sent"))
        self.assertEqual(o.visit.key, "visitkey123")
        self.assertIn("English", call.call_args.args[2]["text"])

    @mock.patch.dict("os.environ", {"TELEGRAM_PROXY": ""})
    @mock.patch("urllib.request.OpenerDirector.open")
    def test_custom_api_url_is_used(self, open_):
        open_.side_effect = lambda *a, **k: mock.MagicMock(**{"__enter__.return_value": io.BytesIO(b'{"ok": true}')})
        s = SiteSettings.load()
        s.telegram_api_url = "https://relay.example.com/tg/"
        s.save()
        self.assertEqual(self.post().status_code, 200)
        self.assertTrue(open_.call_args.args[0].full_url.startswith("https://relay.example.com/tg/bot" + TOKEN + "/sendMessage"))


# =============================================================== backup import / export
class BackupTests(AdminMixin, TransactionTestCase):
    def export(self, media="1"):
        r = self.client.get(reverse("admin:shop_backup_export") + f"?media={media}")
        self.assertEqual(r.status_code, 200)
        return b"".join(r.streaming_content)

    def upload(self, content, name="backup.zip", confirm="yes"):
        f = SimpleUploadedFile(name, content, content_type="application/zip")
        return self.client.post(reverse("admin:shop_backup"), {"file": f, "confirm": confirm}, follow=True)

    def test_export_contains_db_and_media(self):
        self.add_product(images=[png()])
        z = zipfile.ZipFile(io.BytesIO(self.export()))
        names = z.namelist()
        self.assertIn("db.sqlite3", names)
        self.assertTrue(any(n.startswith("media/products/") and n.endswith("-600.webp") for n in names))
        self.assertEqual([n for n in zipfile.ZipFile(io.BytesIO(self.export("0"))).namelist()], ["db.sqlite3"])

    def test_roundtrip_restores_content_and_photos(self):
        self.add_product(images=[png()])
        self.post_settings(hero_title="Версия 1")
        snapshot = self.export()
        # change everything
        Product.objects.all().delete()
        self.post_settings(hero_title="Версия 2")
        self.assertNotIn('data-slug="bumazhnik"', self.page())
        # restore (sessions come from the backup too -> log in again)
        r = self.upload(snapshot)
        self.client.force_login(get_user_model().objects.get(username="owner"))
        html = self.page()
        self.assertIn("Версия 1", html)
        self.assertIn('data-slug="bumazhnik"', html)
        img = Product.objects.get().images.get()
        self.assertTrue(Path(img.image.path).exists())
        self.assertEqual(len(list((settings.DATA_DIR / "backups").glob("before-import-*.zip"))), 1)

    def test_db_only_backup_keeps_photos(self):
        self.add_product(images=[png()])
        snapshot = self.export("0")
        self.upload(snapshot)
        self.assertTrue(Path(ProductImage.objects.get().image.path).exists())

    def test_bare_sqlite_accepted(self):
        self.post_settings(hero_title="Из sqlite")
        db = zipfile.ZipFile(io.BytesIO(self.export("0"))).read("db.sqlite3")
        self.post_settings(hero_title="Другое")
        self.upload(db, name="db.sqlite3")
        self.assertIn("Из sqlite", self.page())

    def test_rejects_bad_files(self):
        before = SiteSettings.load().hero_title
        cases = {
            "мусор": b"not a database at all",
            "zip без базы": self._zip({"hello.txt": b"hi"}),
            "чужая база": self._zip({"db.sqlite3": self._foreign_db()}),
            "обход пути": self._zip({"db.sqlite3": zipfile.ZipFile(io.BytesIO(self.export("0"))).read("db.sqlite3"),
                                     "../evil.txt": b"x"}),
        }
        for label, content in cases.items():
            r = self.upload(content)
            self.assertContains(r, "Импорт не выполнен", msg_prefix=label)
        self.assertContains(self.upload(self.export("0"), confirm=""), "Подтвердите", msg_prefix="без галочки")
        self.assertEqual(SiteSettings.load().hero_title, before)
        self.assertFalse((self._tmp / "evil.txt").exists())

    def test_backup_only_for_superuser(self):
        staff = get_user_model().objects.create_user("editor", password="x" * 12, is_staff=True)
        c = Client()
        c.force_login(staff)
        self.assertEqual(c.get(reverse("admin:shop_backup")).status_code, 403)
        self.assertEqual(c.get(reverse("admin:shop_backup_export")).status_code, 403)
        self.assertEqual(Client().get(reverse("admin:shop_backup")).status_code, 302)  # anonymous -> login

    @staticmethod
    def _zip(files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, data in files.items():
                z.writestr(name, data)
        return buf.getvalue()

    def _foreign_db(self):
        p = self._tmp / "foreign.sqlite3"
        con = sqlite3.connect(p)
        con.execute("create table t(x)")
        con.commit()
        con.close()
        return p.read_bytes()


# =============================================================== access, seo, import command
class AccessAndPagesTests(MediaTmpMixin, TestCase):
    def test_admin_requires_login(self):
        c = Client()
        for name in ("admin:index", "admin:shop_product_changelist", "admin:shop_bot_chats", "admin:shop_backup"):
            r = c.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn("/admin/login/", r["Location"])

    def test_public_pages(self):
        c = Client()
        for url in ("/", "/privacy.html", "/robots.txt", "/sitemap.xml", "/api/health"):
            self.assertEqual(c.get(url).status_code, 200, url)
        self.assertIn("Disallow: /admin/", c.get("/robots.txt").content.decode())


class ImportContentCommandTests(MediaTmpMixin, TestCase):
    def test_import_fixture(self):
        root = self._tmp / "content"
        (root / "img").mkdir(parents=True)
        Image.new("RGB", (400, 300), "red").save(root / "img" / "p1.jpg")
        Image.new("RGB", (1600, 1000), "blue").save(root / "img" / "hero.jpg")
        (root / "content.json").write_text(json.dumps({
            "settings": {"hero_title": "Импортировано", "city": "Город", "bot_token": ""},
            "images": {"hero_image": "hero.jpg"},
            "categories": [{"slug": "docs", "name": "Документы"}],
            "products": [{"slug": "p1", "name": "Товар 1", "cat": "docs", "price": 1000, "desc": ["А", "Б"], "imgs": ["p1.jpg"], "him": True}],
            "perks": [{"title": "П", "text": "т"}], "steps": [{"title": "Ш", "text": "т"}],
            "faq": [{"q": "В?", "a": "О"}], "reviews": [{"author": "Аня", "text": "Супер"}],
        }), encoding="utf-8")
        s = SiteSettings.load()
        s.bot_token = TOKEN
        s.save()
        call_command("import_content", str(root), stdout=io.StringIO())
        self.assertEqual(SiteSettings.load().bot_token, TOKEN)  # configured bot is not overwritten
        html = Client().get("/").content.decode()
        for text in ("Импортировано", "Товар 1", "В?", "Аня", "Документы"):
            self.assertIn(text, html)
        self.assertEqual(Product.objects.get().images.count(), 1)
        with self.assertRaises(Exception):
            call_command("import_content", str(root), stdout=io.StringIO())  # already filled, needs --replace
        call_command("import_content", str(root), "--replace", stdout=io.StringIO())
        self.assertEqual(Product.objects.count(), 1)


class TelegramRobustnessTests(TestCase):
    @mock.patch("urllib.request.OpenerDirector.open")
    def test_non_json_answer_does_not_crash(self, open_):
        from . import telegram
        open_.return_value.__enter__.return_value = io.BytesIO(b"<html>502 Bad Gateway</html>")
        self.assertEqual(telegram.call(TOKEN, "getMe"), {"ok": False, "description": "unexpected response"})

    @mock.patch("urllib.request.OpenerDirector.open", side_effect=OSError(101, "Network is unreachable"))
    def test_network_error_reported(self, open_):
        from . import telegram
        r = telegram.call(TOKEN, "getMe")
        self.assertFalse(r["ok"])
        self.assertIn("Network is unreachable", r["description"])


# =============================================================== English version
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def strip_lang_switch(html):
    """The language switch shows «Русский» on purpose — drop it before looking for untranslated text."""
    html = re.sub(r"<(a|button)[^>]*(data-lang-switch|lang=\"ru\"|value=\"ru\")[^>]*>.*?</\1>", "", html, flags=re.S)
    return re.sub(r'title="Русская версия"', "", html)


def cyrillic_snippets(html):
    return [html[max(0, m.start() - 60):m.end() + 60] for m in CYRILLIC.finditer(html)][:5]


class EnglishVersionTests(AdminMixin, TestCase):
    def fill_bilingual_content(self):
        s = SiteSettings.load()
        for f in ("master_name", "city", "seo_title", "seo_description", "hero_eyebrow", "hero_title", "hero_lead",
                  "about_title", "about_text", "custom_title", "custom_text"):
            setattr(s, f, f"Русский текст {f}")
            setattr(s, f + "_en", f"English text {f}")
        s.telegram, s.vk_url, s.whatsapp, s.phone, s.email = "gl_test", "https://vk.com/gl", "+79130001122", "+7 913 000-11-22", "a@b.co"
        s.old_site_url, s.metrika_id = "https://gluckstal.tilda.ws/gluckstal", "123"
        s.save()
        cat = Category.objects.create(name="Кошельки", name_en="Wallets", slug="wallets")
        p = Product.objects.create(name="Бумажник", name_en="Travel wallet", slug="bumazhnik", category=cat, price=6500,
                                   price_from=True, description="Пункт\nДругой", description_en="Point\nAnother", for_him=True)
        ProductImage.objects.create(product=p, image=png())
        Product.objects.create(name="Клатч", name_en="Clutch", slug="clutch", category=cat, price=1500,
                               description="Клатч", description_en="Clutch bag", for_her=True)
        InfoCard.objects.create(kind="perk", title="Кожа", title_en="Leather", text="Текст", text_en="Text")
        InfoCard.objects.create(kind="step", title="Шаг", title_en="Step", text="Текст", text_en="Text")
        Faq.objects.create(question="Вопрос?", question_en="Question?", answer="Ответ", answer_en="Answer")
        Review.objects.create(author="Анна", author_en="Anna", product_name="Бумажник", product_name_en="Travel wallet",
                              text="Супер", text_en="Great")

    def test_english_page_has_no_russian_left(self):
        self.fill_bilingual_content()
        cache.clear()
        html = Client().get("/en/").content.decode()
        self.assertIn('<html lang="en">', html)
        for text in ("English text hero_title", "Travel wallet", "from 6,500 ₽", "Wallets", "Question?", "Anna",
                     "Great", "Leather", "Step", "Old website (Tilda)", "Privacy policy", 'hreflang="ru"',
                     '"numLocale":"en-US"', "/en/privacy.html"):
            self.assertIn(text, html, text)
        rest = strip_lang_switch(html)
        self.assertEqual(cyrillic_snippets(rest), [])

    def test_russian_page_stays_russian_and_links_english(self):
        self.fill_bilingual_content()
        cache.clear()
        html = Client().get("/").content.decode()
        self.assertIn('<html lang="ru">', html)
        self.assertIn("Русский текст hero_title", html)
        self.assertIn('href="/en/"', html)
        self.assertNotIn("English text hero_title", html)

    def test_fallback_to_russian_when_translation_missing(self):
        cat = Category.objects.create(name="Сумки", slug="bags")
        Product.objects.create(name="Шопер", slug="shopper", category=cat, price=8000, description="Большой")
        cache.clear()
        html = Client().get("/en/").content.decode()
        self.assertIn("Шопер", html)  # no English name yet -> Russian is shown instead of an empty card

    def test_english_privacy_page(self):
        self.fill_bilingual_content()
        html = Client().get("/en/privacy.html").content.decode()
        self.assertIn("Privacy policy", html)
        self.assertIn("English text master_name", html)
        self.assertEqual(cyrillic_snippets(strip_lang_switch(html)), [])

    def test_sitemap_lists_both_languages(self):
        xml = Client().get("/sitemap.xml").content.decode()
        self.assertIn("https://gluckstal.store/</loc>", xml)
        self.assertIn("https://gluckstal.store/en/</loc>", xml)

    def test_admin_in_english_has_no_russian_left(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
        urls = [reverse("admin:index"), reverse("admin:shop_sitesettings_change", args=[SiteSettings.load().pk]),
                reverse("admin:shop_backup")]
        for model in ("product", "category", "review", "faq", "infocard"):
            urls += [reverse(f"admin:shop_{model}_add"), reverse(f"admin:shop_{model}_changelist")]
        urls += [reverse("admin:shop_order_changelist"), reverse("admin:shop_visit_changelist"),
                 reverse("admin:shop_telegramaccount_changelist")]
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertEqual(cyrillic_snippets(strip_lang_switch(r.content.decode())), [], url)  # noqa

    def test_admin_login_page_in_english(self):
        c = Client()
        c.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
        html = c.get(reverse("admin:login")).content.decode()
        self.assertIn("Log in", html)
        self.assertEqual(cyrillic_snippets(strip_lang_switch(html)), [])

    def test_language_switch_endpoint(self):
        r = Client().post("/i18n/setlang/", {"language": "en", "next": "/admin/"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en")

    def test_import_translations_command(self):
        cat = Category.objects.create(name="Кошельки", slug="wallets")
        Product.objects.create(name="Бумажник", slug="bumazhnik", category=cat, price=1, description="А")
        Faq.objects.create(question="Вопрос?", answer="Ответ")
        root = self._tmp / "c"
        root.mkdir()
        (root / "content.json").write_text(json.dumps({
            "settings": {"hero_title_en": "Handmade", "hero_title": "не трогать"},
            "categories": [{"slug": "wallets", "name": "Кошельки", "name_en": "Wallets"}],
            "products": [{"slug": "bumazhnik", "name_en": "Travel wallet", "desc_en": ["One", "Two"]}],
            "faq": [{"q": "Вопрос?", "q_en": "Question?", "a_en": "Answer"}]}), encoding="utf-8")
        call_command("import_content", str(root), "--translations", stdout=io.StringIO())
        p = Product.objects.get()
        self.assertEqual((p.name, p.name_en, p.description_en), ("Бумажник", "Travel wallet", "One\nTwo"))
        self.assertEqual(Category.objects.get().name_en, "Wallets")
        self.assertEqual(Faq.objects.get().answer_en, "Answer")
        self.assertEqual(SiteSettings.load().hero_title_en, "Handmade")
        self.assertEqual(SiteSettings.load().hero_title, "")  # Russian fields untouched


# =============================================================== Telegram login
def signed(data, token=TOKEN):
    import hashlib
    import hmac
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    return {**data, "hash": hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()}


class TelegramLoginTests(MediaTmpMixin, TestCase):
    def setUp(self):
        super().setUp()
        s = SiteSettings.load()
        s.bot_token, s.bot_username = TOKEN, "gl_bot"
        s.save()
        self.user = get_user_model().objects.create_superuser("owner", password="test-only-password-123")

    def widget(self, **extra):
        import time as _t
        return signed({"id": "777", "first_name": "Kira", "username": "kira_tg", "auth_date": str(int(_t.time())), **extra})

    def test_login_page_shows_widget(self):
        html = Client().get(reverse("admin:login")).content.decode()
        self.assertIn('data-telegram-login="gl_bot"', html)
        self.assertIn(reverse("telegram_login"), html)

    def test_link_then_login(self):
        c = Client()
        c.force_login(self.user)
        r = c.get(reverse("telegram_link"), self.widget())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(TelegramAccount.objects.get().telegram_id, 777)
        anon = Client()
        r = anon.get(reverse("telegram_login"), {**self.widget(), "next": "/admin/shop/order/"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/admin/shop/order/")
        self.assertEqual(anon.get(reverse("admin:index")).status_code, 200)  # logged in
        self.assertIsNotNone(TelegramAccount.objects.get().last_login)

    def test_rejects_forged_unknown_expired_and_open_redirect(self):
        TelegramAccount.objects.create(user=self.user, telegram_id=777)
        forged = {**self.widget(), "id": "778"}  # changed after signing
        wrong_key = signed({"id": "777", "auth_date": str(int(__import__("time").time()))}, token=TOKEN[:-1] + "X")
        expired = signed({"id": "777", "auth_date": "1000"})
        for data in (forged, wrong_key, expired, {"id": "777"}):
            c = Client()
            r = c.get(reverse("telegram_login"), data)
            self.assertEqual(r["Location"], reverse("admin:login"), data)
            self.assertEqual(c.get(reverse("admin:index")).status_code, 302)  # still anonymous
        unknown = self.widget(id="999")
        self.assertEqual(Client().get(reverse("telegram_login"), unknown)["Location"], reverse("admin:login"))
        r = Client().get(reverse("telegram_login"), {**self.widget(), "next": "https://evil.example/"})
        self.assertEqual(r["Location"], reverse("admin:index"))

    def test_non_staff_cannot_login(self):
        u = get_user_model().objects.create_user("visitor", password="x" * 12)
        TelegramAccount.objects.create(user=u, telegram_id=777)
        c = Client()
        c.get(reverse("telegram_login"), self.widget())
        self.assertEqual(c.get(reverse("admin:index")).status_code, 302)

    def test_link_requires_staff_and_unlink(self):
        self.assertEqual(Client().get(reverse("telegram_link"), self.widget()).status_code, 403)
        c = Client()
        c.force_login(self.user)
        c.get(reverse("telegram_link"), self.widget())
        self.assertIn("@kira_tg", c.get(reverse("admin:index")).content.decode())
        c.post(reverse("telegram_unlink"))
        self.assertFalse(TelegramAccount.objects.exists())


# =============================================================== visit tracking and dashboard
@override_settings(ALLOWED_HOSTS=["testserver"])
class AnalyticsTests(MediaTmpMixin, TestCase):
    UA_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                 "Version/17.5 Mobile/15E148 Safari/604.1")

    def beacon(self, c=None, ua=UA_IPHONE, **d):
        c = c or Client()
        return c.post("/api/track", json.dumps({"v": "visit0001", "u": "person001", **d}), content_type="text/plain",
                      HTTP_USER_AGENT=ua, HTTP_X_FORWARDED_FOR="203.0.113.7")

    def test_visit_lifecycle(self):
        self.assertEqual(self.beacon(t="start", url="https://gluckstal.store/?utm_source=vk", ref="", lang="en", scr="390x844").status_code, 204)
        self.beacon(t="ping", a=15)
        self.beacon(t="event", e="product_open", a=20)
        self.beacon(t="event", e="product_open", a=25)
        self.beacon(t="event", e="evil<script>", a=26)
        self.beacon(t="end", a=42)
        v = Visit.objects.get()
        self.assertEqual((v.ip, v.device_type, v.source, v.lang, v.active_seconds), ("203.0.113.7", "mobile", "vk", "en", 42))
        self.assertIn("iPhone", v.device)
        self.assertIn("iOS 17", v.device)
        self.assertEqual(v.events, {"product_open": 2})

    def test_bots_and_bad_input_ignored(self):
        self.beacon(t="start", ua="Mozilla/5.0 (compatible; Googlebot/2.1)")
        self.assertFalse(Visit.objects.exists())
        self.assertEqual(Client().post("/api/track", "{", content_type="text/plain").status_code, 400)
        self.assertEqual(Client().post("/api/track", json.dumps({"v": "../x", "u": "y"}), content_type="text/plain").status_code, 400)
        self.beacon(t="ping", a=10)  # ping for an unknown visit does not create one
        self.assertFalse(Visit.objects.exists())

    def test_staff_visits_flagged_and_excluded(self):
        admin_user = get_user_model().objects.create_superuser("owner", password="test-only-password-123")
        c = Client()
        c.force_login(admin_user)
        self.beacon(c=c, t="start", url="https://gluckstal.store/")
        self.assertTrue(Visit.objects.get().is_staff)
        from .dashboard import stats
        self.assertEqual(stats()["today"]["visits"], 0)

    def test_parse_ua(self):
        from .analytics import parse_ua
        self.assertEqual(parse_ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/128.0 YaBrowser/24.7 Safari/537.36"), ("Windows · Yandex Browser", "desktop"))
        label, kind = parse_ua("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/126.0 Mobile Safari/537.36")
        self.assertEqual(kind, "mobile")
        self.assertIn("SM-S918B", label)
        self.assertEqual(parse_ua("curl/8.5"), ("Bot", "bot"))

    @mock.patch("shop.telegram.call", return_value={"ok": True})
    def test_dashboard_numbers(self, call):
        self.beacon(t="start", url="https://gluckstal.store/")
        self.beacon(t="event", e="tg_click", a=30)
        self.beacon(t="end", a=90)
        s = SiteSettings.load()
        s.bot_token, s.chat_ids = TOKEN, "1"
        s.save()
        Client().post("/api/order", json.dumps({"name": "Анна", "contact": "@a", "consent": "on", "elapsed": 9000,
                                                "visit": "visit0001"}), content_type="application/json")
        from .dashboard import stats
        d = stats()
        self.assertEqual((d["today"]["visits"], d["today"]["visitors"], d["today"]["orders"], d["today"]["tg"]), (1, 1, 1, 1))
        self.assertEqual(d["today"]["avg_time"], "1:30")
        self.assertEqual(d["today"]["conversion"], "100.0%")
        self.assertEqual(d["chart"][-1]["visits"], 1)
        admin_user = get_user_model().objects.create_superuser("owner", password="test-only-password-123")
        c = Client()
        c.force_login(admin_user)
        html = c.get(reverse("admin:index")).content.decode()
        for text in ("203.0.113.7", "iPhone", "1:30", "Анна"):
            self.assertIn(text, html, text)

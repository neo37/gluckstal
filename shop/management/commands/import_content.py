"""Initial content load from a content directory (content is not stored in git).

Directory layout:
  content.json   — {"settings": {...}, "images": {field: file}, "categories": [...], "products": [...],
                    "perks": [...], "steps": [...], "faq": [...], "reviews": [...]}
                   Any translatable field may have an English twin with the "_en" suffix.
  img/           — photo files referenced by content.json

  python manage.py import_content /path/to/content [--replace]
  python manage.py import_content /path/to/content --translations   # only fill *_en fields of existing rows
"""
import json
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from shop.models import Category, Faq, InfoCard, Product, ProductImage, Review, SiteSettings


class Command(BaseCommand):
    help = "Import site content from a directory (content.json + img/)"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--replace", action="store_true", help="delete the current catalog/FAQ/blocks before importing")
        parser.add_argument("--translations", action="store_true", help="only fill *_en fields of existing rows")

    def handle(self, path, replace, translations=False, **kw):
        root = Path(path)
        try:
            data = json.loads((root / "content.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise CommandError(f"{root / 'content.json'} not found")
        img = lambda name: File(open(root / "img" / name, "rb"), name=name)
        if translations:
            return self.load_translations(data)

        with transaction.atomic():
            if replace:
                for m in (ProductImage, Product, Category, InfoCard, Faq):
                    for obj in m.objects.all():
                        obj.delete()
            elif Product.objects.exists():
                raise CommandError("the catalog is not empty; use --replace to overwrite it")

            s = SiteSettings.load()
            for k, v in data.get("settings", {}).items():
                if k in ("bot_token", "chat_ids") and getattr(s, k):
                    continue  # never overwrite an already configured bot
                setattr(s, k, v)
            for field, name in data.get("images", {}).items():
                getattr(s, field).save(name, img(name), save=False)
            s.full_clean()
            s.save()

            cats = {}
            for n, c in enumerate(data.get("categories", [])):
                cats[c["slug"]] = Category.objects.create(name=c["name"], name_en=c.get("name_en", ""), slug=c["slug"], order=n * 10)
            for n, p in enumerate(data.get("products", [])):
                prod = Product.objects.create(
                    name=p["name"], slug=p["slug"], category=cats[p["cat"]], price=p["price"],
                    price_from=p.get("priceFrom", False), description="\n".join(p["desc"]),
                    name_en=p.get("name_en", ""), description_en="\n".join(p.get("desc_en", [])),
                    for_him=p.get("him", False), for_her=p.get("her", False), order=n * 10)
                for i, name in enumerate(p.get("imgs", [])):
                    pi = ProductImage(product=prod, order=i)
                    pi.image.save(name, img(name), save=False)
                    pi.save()
                self.stdout.write(f"  {prod.name}: {len(p.get('imgs', []))} photos")
            for kind in ("perk", "step"):
                for n, c in enumerate(data.get(kind + "s", [])):
                    InfoCard.objects.create(kind=kind, title=c["title"], text=c["text"], title_en=c.get("title_en", ""),
                                            text_en=c.get("text_en", ""), order=n * 10)
            for n, f in enumerate(data.get("faq", [])):
                Faq.objects.create(question=f["q"], answer=f["a"], question_en=f.get("q_en", ""), answer_en=f.get("a_en", ""),
                                   order=n * 10)
            for n, r in enumerate(data.get("reviews", [])):
                rv = Review(author=r["author"], product_name=r.get("product", ""), text=r["text"], text_en=r.get("text_en", ""),
                            author_en=r.get("author_en", ""), product_name_en=r.get("product_en", ""),
                            link=r.get("link", ""), visible=r.get("visible", True), order=n * 10)
                if r.get("photo"):
                    rv.photo.save(r["photo"], img(r["photo"]), save=False)
                rv.save()

        self.stdout.write(self.style.SUCCESS(
            f"Done: {Product.objects.count()} products, {ProductImage.objects.count()} photos, "
            f"{Faq.objects.count()} FAQ entries, {Review.objects.count()} reviews"))

    def load_translations(self, data):
        """Fill English fields of rows that already exist (matched by slug / position / Russian text)."""
        n = 0
        with transaction.atomic():
            s = SiteSettings.load()
            for k, v in data.get("settings", {}).items():
                if k.endswith("_en"):
                    setattr(s, k, v)
                    n += 1
            s.save()
            for c in data.get("categories", []):
                n += Category.objects.filter(slug=c["slug"]).update(name_en=c.get("name_en", ""))
            for p in data.get("products", []):
                n += Product.objects.filter(slug=p["slug"]).update(
                    name_en=p.get("name_en", ""), description_en="\n".join(p.get("desc_en", [])))
            for kind in ("perk", "step"):
                for c in data.get(kind + "s", []):
                    n += InfoCard.objects.filter(kind=kind, title=c["title"]).update(
                        title_en=c.get("title_en", ""), text_en=c.get("text_en", ""))
            for f in data.get("faq", []):
                n += Faq.objects.filter(question=f["q"]).update(question_en=f.get("q_en", ""), answer_en=f.get("a_en", ""))
        # .update() bypasses signals — drop the cached pages explicitly
        from django.core.cache import cache
        cache.clear()
        self.stdout.write(self.style.SUCCESS(f"Translations loaded: {n} rows/fields updated"))

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import login
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, JsonResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from . import backup, telegram
from .images import variant_url
from .models import (Category, Faq, InfoCard, Order, Product, ProductImage, Review, SiteSettings, TelegramAccount,
                     Visit)


def thumb(field, size=64):
    if not field:
        return "—"
    return format_html('<img src="{}" style="width:{}px;height:{}px;object-fit:cover;border-radius:6px" alt="">',
                       variant_url(field, 600), size, size)


# ================================================================ site settings (single row)
class SiteSettingsForm(forms.ModelForm):
    bot_token = forms.CharField(
        label=gettext_lazy("Токен Telegram-бота"), required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off", "placeholder": "123456789:AA…"}),
        help_text=gettext_lazy("Из @BotFather. Сохранённый токен не показывается: оставьте поле пустым, чтобы не менять."))
    clear_token = forms.BooleanField(label=gettext_lazy("Удалить сохранённый токен"), required=False)

    class Meta:
        model = SiteSettings
        fields = "__all__"

    def clean_bot_token(self):
        new = self.cleaned_data.get("bot_token", "").strip()
        if new:
            SiteSettings._meta.get_field("bot_token").run_validators(new)
            return new
        return self.instance.bot_token  # empty field — keep the saved one

    def clean(self):
        data = super().clean()
        if data.get("clear_token"):
            data["bot_token"] = ""
        return data


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    form = SiteSettingsForm
    change_form_template = "admin/shop/sitesettings/change_form.html"
    fieldsets = [
        (gettext_lazy("Заявки в Telegram"), {
            "fields": ["bot_token", "clear_token", "chat_ids", "bot_username", "telegram_api_url"],
            "description": gettext_lazy("1) Создайте бота в @BotFather и вставьте токен. 2) С аккаунта, куда нужны заявки, откройте "
                              "бота и нажмите Start. 3) Нажмите «Найти чаты» вверху → «Добавить» → «Сохранить». "
                              "4) «Отправить тест».")}),
        (gettext_lazy("Контакты"), {"fields": ["brand", ("master_name", "master_name_en"), ("city", "city_en"), "phone", "email",
                                     "telegram", "vk_url", "whatsapp", "old_site_url"]}),
        (gettext_lazy("Первый экран"), {"fields": [("hero_eyebrow", "hero_eyebrow_en"), ("hero_title", "hero_title_en"),
                                         ("hero_lead", "hero_lead_en"), "hero_image", "logo"]}),
        (gettext_lazy("Обо мне"), {"fields": [("about_title", "about_title_en"), ("about_text", "about_text_en"),
                                    "about_photo", "workshop_photo"]}),
        (gettext_lazy("Блок «Своя модель»"), {"fields": [("custom_title", "custom_title_en"), ("custom_text", "custom_text_en"),
                                               "custom_photo"]}),
        (gettext_lazy("Поиск и аналитика"), {
            "fields": ["site_url", ("seo_title", "seo_title_en"), ("seo_description", "seo_description_en"), "metrika_id"],
            "description": gettext_lazy("Метрика: создайте счётчик на metrika.yandex.ru и вставьте номер. Цели (JavaScript-событие): "
                              "order_sent, order_click, tg_click, wa_click, product_open, cta_header.")}),
    ]

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        return redirect(reverse("admin:shop_sitesettings_change", args=[SiteSettings.load().pk]))

    def get_urls(self):
        view = self.admin_site.admin_view
        return [
            path("bot/chats/", view(self.bot_chats), name="shop_bot_chats"),
            path("bot/test/", view(self.bot_test), name="shop_bot_test"),
            path("backup/", view(self.backup_page), name="shop_backup"),
            path("backup/export/", view(self.backup_export), name="shop_backup_export"),
        ] + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        s = SiteSettings.load()
        extra_context = {**(extra_context or {}), "token_set": bool(s.bot_token),
                         "token_hint": f"{s.bot_token.split(':')[0]}:…{s.bot_token[-4:]}" if s.bot_token else ""}
        return super().change_view(request, object_id, form_url, extra_context)

    # --- Telegram tools
    def bot_chats(self, request):
        s = SiteSettings.load()
        if not s.bot_token:
            return JsonResponse({"error": _("Сначала сохраните токен бота")}, status=422)
        try:
            bot, chats = telegram.find_chats(s)
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=422)
        if s.bot_username != bot:
            s.bot_username = bot
            s.save(update_fields=["bot_username"])
        return JsonResponse({"bot": bot, "chats": chats})

    def bot_test(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "POST only"}, status=405)
        s = SiteSettings.load()
        if not s.bot_token:
            return JsonResponse({"error": _("Сначала сохраните токен бота")}, status=422)
        try:
            bot = telegram.get_me(s)
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=422)
        if not s.chat_list():
            return JsonResponse({"error": _("Бот @%s работает, но не сохранён ни один ID чата") % bot}, status=422)
        res = telegram.send_all(s, _("✅ Тестовое сообщение из админки сайта %s. Заявки будут приходить сюда.") % s.brand)
        return JsonResponse({"bot": bot, "results": res})

    # --- backup: export / import of the whole site database
    def backup_page(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied
        if request.method == "POST":
            f = request.FILES.get("file")
            if not f:
                messages.error(request, _("Выберите файл резервной копии (.zip или .sqlite3)"))
            elif request.POST.get("confirm") != "yes":
                messages.error(request, _("Подтвердите замену данных галочкой"))
            else:
                try:
                    auto, with_media = backup.import_upload(f)
                except ValueError as e:
                    messages.error(request, _("Импорт не выполнен: %s") % e)
                else:
                    messages.success(request, (_("База сайта восстановлена вместе с фото.") if with_media else
                                               _("База сайта восстановлена (фото не менялись).")) + " "
                                     + _("Предыдущее состояние сохранено: %s. Войдите заново, если попросит.") % auto.name)
                    return redirect("admin:shop_backup")
        ctx = {**self.admin_site.each_context(request), "title": _("Резервная копия сайта"),
               "backups": [{"name": b.name, "size": b.stat().st_size} for b in backup.list_backups()]}
        return TemplateResponse(request, "admin/shop/backup.html", ctx)

    def backup_export(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied
        name = request.GET.get("file")
        if name:  # download an automatic copy made before an import
            f = backup.backups_dir() / name
            if f.parent != backup.backups_dir() or not f.is_file():
                raise PermissionDenied
            return FileResponse(open(f, "rb"), as_attachment=True, filename=f.name)
        z = backup.export_zip(with_media=request.GET.get("media", "1") == "1")
        resp = FileResponse(open(z, "rb"), as_attachment=True, filename=z.name)
        z.unlink()  # already open — safe to unlink on Linux
        z.parent.rmdir()
        return resp


# ================================================================ catalog
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "name_en", "slug", "order", "count"]
    list_editable = ["order"]
    fields = [("name", "name_en"), "slug", "order"]
    prepopulated_fields = {"slug": ["name"]}

    @admin.display(description=gettext_lazy("Товаров"))
    def count(self, obj):
        return obj.products.count()


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ["preview", "image", "order"]
    readonly_fields = ["preview"]

    @admin.display(description="")
    def preview(self, obj):
        return thumb(obj.image, 80) if obj.pk else ""


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["cover", "name", "category", "price", "visible", "order", "has_en"]
    list_display_links = ["cover", "name"]
    list_editable = ["price", "visible", "order"]
    list_filter = ["category", "visible", "for_him", "for_her"]
    search_fields = ["name", "name_en", "description"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [ProductImageInline]
    fieldsets = [
        (None, {"fields": [("name", "name_en"), "slug", "category", ("price", "price_from"),
                           ("description", "description_en"), "visible", "order"]}),
        (gettext_lazy("Подборки подарков"), {"fields": [("for_him", "for_her")]}),
    ]
    save_on_top = True
    actions = ["make_visible", "make_hidden"]

    @admin.display(description=gettext_lazy("Фото"))
    def cover(self, obj):
        img = obj.images.first()
        return thumb(img.image if img else None, 48)

    @admin.display(description="EN", boolean=True)
    def has_en(self, obj):
        return bool(obj.name_en and obj.description_en)

    @admin.action(description=gettext_lazy("Показать на сайте"))
    def make_visible(self, request, qs):
        for p in qs:
            p.visible = True
            p.save()

    @admin.action(description=gettext_lazy("Скрыть с сайта"))
    def make_hidden(self, request, qs):
        for p in qs:
            p.visible = False
            p.save()


# ================================================================ reviews, FAQ, blocks
@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["preview", "author", "product_name", "short", "visible", "order"]
    list_display_links = ["preview", "author"]
    list_editable = ["visible", "order"]
    fields = [("author", "author_en"), ("product_name", "product_name_en"), ("text", "text_en"), "photo", "link",
              "visible", "order"]

    @admin.display(description=gettext_lazy("Фото"))
    def preview(self, obj):
        return thumb(obj.photo, 48)

    @admin.display(description=gettext_lazy("Текст"))
    def short(self, obj):
        return obj.text[:80] + ("…" if len(obj.text) > 80 else "")


@admin.register(Faq)
class FaqAdmin(admin.ModelAdmin):
    list_display = ["question", "visible", "order"]
    list_editable = ["visible", "order"]
    fields = [("question", "question_en"), ("answer", "answer_en"), "visible", "order"]


@admin.register(InfoCard)
class InfoCardAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "order"]
    list_editable = ["order"]
    list_filter = ["kind"]
    fields = ["kind", ("title", "title_en"), ("text", "text_en"), "order"]


# ================================================================ orders and visits
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["created", "name", "contact", "via", "product", "status", "tg_state", "lang"]
    list_editable = ["status"]
    list_filter = ["status", "telegram", "lang", "created"]
    search_fields = ["name", "contact", "product", "comment"]
    readonly_fields = ["created", "name", "contact", "via", "product", "comment", "lang", "ip", "visit_link",
                       "telegram", "telegram_error"]
    fields = ["created", ("name", "contact", "via"), "product", "comment", ("status", "note"), ("lang", "ip"),
              "visit_link", ("telegram", "telegram_error")]
    date_hierarchy = "created"

    def has_add_permission(self, request):
        return False

    @admin.display(description=gettext_lazy("Telegram"))
    def tg_state(self, obj):
        icon = {"sent": "✅", "failed": "⚠️", "off": "—"}[obj.telegram]
        return format_html('<span title="{}">{} {}</span>', obj.telegram_error, icon, obj.get_telegram_display())

    @admin.display(description=gettext_lazy("Визит"))
    def visit_link(self, obj):
        if not obj.visit_id:
            return "—"
        return format_html('<a href="{}">{}</a>', reverse("admin:shop_visit_change", args=[obj.visit_id]), obj.visit)


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ["started", "ip", "device", "duration", "pageviews", "source", "lang", "events_short", "ordered"]
    list_filter = ["device_type", "source", "lang", "is_staff", "started"]
    search_fields = ["ip", "device", "user_agent", "referrer", "visitor"]
    date_hierarchy = "started"
    readonly_fields = [f.name for f in Visit._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("orders")

    @admin.display(description=gettext_lazy("Время на сайте"), ordering="active_seconds")
    def duration(self, obj):
        return obj.duration_label

    @admin.display(description=gettext_lazy("События"))
    def events_short(self, obj):
        return ", ".join(f"{k}×{v}" for k, v in (obj.events or {}).items()) or "—"

    @admin.display(description=gettext_lazy("Заявка"), boolean=True)
    def ordered(self, obj):
        return bool(obj.orders.all())


@admin.register(TelegramAccount)
class TelegramAccountAdmin(admin.ModelAdmin):
    list_display = ["user", "username", "first_name", "telegram_id", "linked", "last_login"]
    readonly_fields = ["telegram_id", "username", "first_name", "linked", "last_login"]


# ================================================================ login via Telegram
WIDGET_FIELDS = ("id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash")


def _widget_data(request):
    """Signed fields from the Telegram Login Widget redirect (anything else, e.g. ?next=, is not signed)."""
    s = SiteSettings.load()
    if not s.bot_token:
        return None
    return telegram.check_login({k: v for k, v in request.GET.items() if k in WIDGET_FIELDS}, s.bot_token)


def telegram_login(request):
    """Callback of the Telegram Login Widget on the admin login page (anonymous)."""
    nxt = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(nxt, {request.get_host()}, require_https=request.is_secure()):
        nxt = reverse("admin:index")
    data = _widget_data(request)
    if not data:
        messages.error(request, _("Вход через Telegram не удался: подпись неверна или устарела."))
        return redirect("admin:login")
    acc = TelegramAccount.objects.select_related("user").filter(telegram_id=data["id"]).first()
    if not acc or not acc.user.is_active or not acc.user.is_staff:
        messages.error(request, _("Этот Telegram-аккаунт не привязан к администратору. Войдите по паролю и привяжите его "
                                  "на главной странице админки."))
        return redirect("admin:login")
    acc.last_login = timezone.now()
    acc.username = data.get("username", acc.username)
    acc.first_name = data.get("first_name", acc.first_name)
    acc.save(update_fields=["last_login", "username", "first_name"])
    login(request, acc.user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect(nxt)


def telegram_link(request):
    """Link the logged-in admin to the Telegram account from the widget."""
    if not (request.user.is_authenticated and request.user.is_staff):
        raise PermissionDenied
    data = _widget_data(request)
    if not data:
        messages.error(request, _("Не удалось привязать Telegram: подпись неверна или устарела."))
        return redirect("admin:index")
    other = TelegramAccount.objects.filter(telegram_id=data["id"]).exclude(user=request.user).first()
    if other:
        messages.error(request, _("Этот Telegram уже привязан к пользователю %s.") % other.user)
        return redirect("admin:index")
    TelegramAccount.objects.update_or_create(user=request.user, defaults={
        "telegram_id": data["id"], "username": data.get("username", ""), "first_name": data.get("first_name", "")})
    messages.success(request, _("Telegram привязан: теперь можно входить в админку кнопкой «Войти через Telegram»."))
    return redirect("admin:index")


def telegram_unlink(request):
    if request.method != "POST" or not (request.user.is_authenticated and request.user.is_staff):
        raise PermissionDenied
    TelegramAccount.objects.filter(user=request.user).delete()
    messages.success(request, _("Telegram отвязан."))
    return redirect("admin:index")

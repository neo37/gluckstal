from django.conf import settings as dj_settings
from django.core.cache import cache
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.utils.text import format_lazy
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from .images import make_variants, remove_variants

phone_digits = RegexValidator(r"^\+?[\d\s()-]{10,20}$", _("Телефон: 10–15 цифр, с кодом страны"))
EN_HELP = _("Английская версия. Если пусто — на английской странице показывается русский текст.")


def en(field, **kw):
    """English twin of a translatable field (same type, optional)."""
    cls = type(field)
    opts = {"blank": True, "help_text": EN_HELP, **kw}
    if isinstance(field, models.CharField):
        opts["max_length"] = field.max_length
    return cls(format_lazy("{} (EN)", field.verbose_name), **opts)


class Translatable:
    """obj.tr("name") -> name_en on the English site (if filled), otherwise name."""

    def tr(self, field):
        if (get_language() or "").startswith("en"):
            value = getattr(self, f"{field}_en", "")
            if value:
                return value
        return getattr(self, field)


class SiteSettings(Translatable, models.Model):
    """The only row: texts, contacts, photos and integrations of the site."""

    # --- contacts
    brand = models.CharField(_("Название бренда"), max_length=60, default="GLÜCKSTAL")
    master_name = models.CharField(_("Имя мастера"), max_length=80, blank=True)
    master_name_en = en(master_name)
    city = models.CharField(_("Город"), max_length=60, blank=True)
    city_en = en(city)
    phone = models.CharField(_("Телефон"), max_length=30, blank=True, validators=[phone_digits])
    email = models.EmailField(_("E-mail"), blank=True)
    telegram = models.CharField(_("Telegram (ник без @)"), max_length=40, blank=True,
                                validators=[RegexValidator(r"^[A-Za-z0-9_]{4,}$", _("Только ник, без @ и ссылки"))])
    vk_url = models.URLField(_("Ссылка на VK"), blank=True)
    whatsapp = models.CharField(_("WhatsApp (номер)"), max_length=30, blank=True, validators=[phone_digits],
                                help_text=_("Если заполнено — на сайте появится кнопка WhatsApp"))
    old_site_url = models.URLField(_("Ссылка на старый сайт"), blank=True, help_text=_("Показывается в подвале"))

    # --- hero and SEO
    site_url = models.URLField(_("Адрес сайта"), default="https://gluckstal.store",
                               help_text=_("Для canonical, sitemap и превью ссылок"))
    seo_title = models.CharField(_("Заголовок вкладки / для поиска (title)"), max_length=160, blank=True)
    seo_title_en = en(seo_title)
    seo_description = models.CharField(_("Описание для поиска (description)"), max_length=300, blank=True)
    seo_description_en = en(seo_description)
    hero_eyebrow = models.CharField(_("Надзаголовок"), max_length=120, blank=True)
    hero_eyebrow_en = en(hero_eyebrow)
    hero_title = models.CharField(_("Главный заголовок"), max_length=120, blank=True)
    hero_title_en = en(hero_title)
    hero_lead = models.TextField(_("Подзаголовок"), blank=True)
    hero_lead_en = en(hero_lead)
    hero_image = models.ImageField(_("Главное фото"), upload_to="site/", blank=True)
    logo = models.ImageField(_("Логотип (круглый, PNG с прозрачностью)"), upload_to="site/", blank=True)

    # --- about
    about_title = models.CharField(_("Заголовок «Обо мне»"), max_length=120, blank=True)
    about_title_en = en(about_title)
    about_text = models.TextField(_("Текст «Обо мне»"), blank=True,
                                  help_text=_("Абзацы — через пустую строку. **Жирный** — двумя звёздочками."))
    about_text_en = en(about_text)
    about_photo = models.ImageField(_("Фото мастера"), upload_to="site/", blank=True)
    workshop_photo = models.ImageField(_("Фото мастерской"), upload_to="site/", blank=True)

    # --- custom model block
    custom_title = models.CharField(_("Заголовок блока «Своя модель»"), max_length=120, blank=True)
    custom_title_en = en(custom_title)
    custom_text = models.TextField(_("Текст блока «Своя модель»"), blank=True)
    custom_text_en = en(custom_text)
    custom_photo = models.ImageField(_("Фото блока «Своя модель»"), upload_to="site/", blank=True)

    # --- integrations
    metrika_id = models.CharField(_("Номер счётчика Яндекс.Метрики"), max_length=20, blank=True,
                                  validators=[RegexValidator(r"^\d+$", _("Только цифры"))])
    bot_token = models.CharField(_("Токен Telegram-бота"), max_length=120, blank=True,
                                 validators=[RegexValidator(r"^\d{5,}:[A-Za-z0-9_-]{30,}$", _("Формат 123456789:AA…"))])
    chat_ids = models.CharField(_("ID чатов для заявок"), max_length=300, blank=True,
                                validators=[RegexValidator(r"^\s*(-?\d{3,}|@\w{5,})(\s*,\s*(-?\d{3,}|@\w{5,}))*\s*$",
                                                           _("Номера через запятую"))],
                                help_text=_("Кому бот присылает заявки. Проще всего — кнопкой «Найти чаты» вверху страницы."))
    bot_username = models.CharField(_("Username бота (без @)"), max_length=64, blank=True,
                                    help_text=_("Нужен для входа в админку через Telegram. Заполняется сам при «Найти чаты»."))
    telegram_api_url = models.URLField(_("Адрес Telegram Bot API"), blank=True,
                                       help_text=_("Пусто — https://api.telegram.org. Укажите адрес ретранслятора, "
                                                   "если сервер не может напрямую связаться с Telegram."))

    class Meta:
        verbose_name = verbose_name_plural = _("Настройки сайта")

    def __str__(self):
        return str(_("Настройки сайта"))

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def chat_list(self):
        return [c.strip() for c in self.chat_ids.split(",") if c.strip()]

    @property
    def phone_raw(self):
        return "+" + "".join(ch for ch in self.phone if ch.isdigit()) if self.phone else ""

    @property
    def whatsapp_digits(self):
        return "".join(ch for ch in self.whatsapp if ch.isdigit())

    IMAGE_VARIANTS = {"hero_image": (900, 1600), "about_photo": (900,), "workshop_photo": (900,),
                      "custom_photo": (900,), "logo": (240,)}

    def save(self, *a, **kw):
        self.pk = 1
        super().save(*a, **kw)
        for field, widths in self.IMAGE_VARIANTS.items():
            f = getattr(self, field)
            if f:
                make_variants(f.path, widths, og=(field == "hero_image"))


class Category(Translatable, models.Model):
    name = models.CharField(_("Название"), max_length=60)
    name_en = en(name)
    slug = models.SlugField(_("Код (латиницей)"), max_length=40, unique=True)
    order = models.PositiveIntegerField(_("Порядок"), default=0)

    class Meta:
        verbose_name, verbose_name_plural = _("Категория"), _("Категории")
        ordering = ["order", "id"]

    def __str__(self):
        return self.name


class Product(Translatable, models.Model):
    name = models.CharField(_("Название"), max_length=120)
    name_en = en(name)
    slug = models.SlugField(_("Код для ссылки (латиницей)"), max_length=60, unique=True,
                            help_text=_("Ссылка на товар будет вида /#p=код"))
    category = models.ForeignKey(Category, verbose_name=_("Категория"), on_delete=models.PROTECT, related_name="products")
    price = models.PositiveIntegerField(_("Цена, ₽"))
    price_from = models.BooleanField(_("Цена «от»"), default=False)
    description = models.TextField(_("Описание"), help_text=_("Каждый пункт — с новой строки. Первый пункт виден в карточке."))
    description_en = en(description)
    for_him = models.BooleanField(_("Подарок мужчине"), default=False)
    for_her = models.BooleanField(_("Подарок женщине"), default=False)
    visible = models.BooleanField(_("Показывать"), default=True)
    order = models.PositiveIntegerField(_("Порядок"), default=0)

    class Meta:
        verbose_name, verbose_name_plural = _("Товар"), _("Товары")
        ordering = ["order", "id"]

    def __str__(self):
        return self.name

    def bullets(self):
        return [line.strip(" •-֍\t") for line in self.tr("description").splitlines() if line.strip(" •-֍\t")]


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(_("Фото"), upload_to="products/")
    order = models.PositiveIntegerField(_("Порядок"), default=0)

    class Meta:
        verbose_name, verbose_name_plural = _("Фото"), _("Фото")
        ordering = ["order", "id"]

    def __str__(self):
        return self.image.name

    def save(self, *a, **kw):
        super().save(*a, **kw)
        make_variants(self.image.path, (600, 1200))


class Review(Translatable, models.Model):
    author = models.CharField(_("Имя автора"), max_length=80)
    author_en = en(author)
    product_name = models.CharField(_("Изделие"), max_length=120, blank=True)
    product_name_en = en(product_name)
    text = models.TextField(_("Текст отзыва"))
    text_en = en(text)
    photo = models.ImageField(_("Фото"), upload_to="reviews/", blank=True)
    link = models.URLField(_("Ссылка на оригинал"), blank=True)
    visible = models.BooleanField(_("Показывать"), default=True)
    order = models.PositiveIntegerField(_("Порядок"), default=0)

    class Meta:
        verbose_name, verbose_name_plural = _("Отзыв"), _("Отзывы")
        ordering = ["order", "-id"]

    def __str__(self):
        return f"{self.author}: {self.text[:40]}"

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if self.photo:
            make_variants(self.photo.path, (600,))


class InfoCard(Translatable, models.Model):
    KINDS = [("perk", _("Преимущество (тёмная полоса)")), ("step", _("Шаг «Как заказать»"))]
    kind = models.CharField(_("Блок"), max_length=10, choices=KINDS)
    title = models.CharField(_("Заголовок"), max_length=80)
    title_en = en(title)
    text = models.TextField(_("Текст"))
    text_en = en(text)
    order = models.PositiveIntegerField(_("Порядок"), default=0)

    class Meta:
        verbose_name, verbose_name_plural = _("Карточка блока"), _("Преимущества и шаги заказа")
        ordering = ["kind", "order", "id"]

    def __str__(self):
        return self.title


class Faq(Translatable, models.Model):
    question = models.CharField(_("Вопрос"), max_length=200)
    question_en = en(question)
    answer = models.TextField(_("Ответ"))
    answer_en = en(answer)
    visible = models.BooleanField(_("Показывать"), default=True)
    order = models.PositiveIntegerField(_("Порядок"), default=0)

    class Meta:
        verbose_name, verbose_name_plural = _("Вопрос"), _("Частые вопросы")
        ordering = ["order", "id"]

    def __str__(self):
        return self.question


class Order(models.Model):
    STATUSES = [("new", _("Новая")), ("work", _("В работе")), ("done", _("Выполнена")), ("cancel", _("Отменена"))]
    TG = [("sent", _("Отправлена в Telegram")), ("failed", _("Ошибка Telegram")), ("off", _("Бот не настроен"))]
    created = models.DateTimeField(_("Создана"), auto_now_add=True, db_index=True)
    name = models.CharField(_("Имя"), max_length=80)
    contact = models.CharField(_("Контакт"), max_length=120)
    via = models.CharField(_("Связаться через"), max_length=20, blank=True)
    product = models.CharField(_("Изделие"), max_length=120, blank=True)
    comment = models.TextField(_("Пожелания"), blank=True)
    lang = models.CharField(_("Язык сайта"), max_length=5, blank=True)
    ip = models.GenericIPAddressField(_("IP"), null=True, blank=True)
    visit = models.ForeignKey("Visit", verbose_name=_("Визит"), null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="orders")
    status = models.CharField(_("Статус"), max_length=10, choices=STATUSES, default="new", db_index=True)
    telegram = models.CharField(_("Telegram"), max_length=10, choices=TG, default="off")
    telegram_error = models.CharField(_("Ошибка Telegram"), max_length=300, blank=True)
    note = models.TextField(_("Заметка"), blank=True)

    class Meta:
        verbose_name, verbose_name_plural = _("Заявка"), _("Заявки")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.name} — {self.product or self.contact}"


class Visit(models.Model):
    """One browsing session (a new one after 30 minutes of inactivity)."""
    key = models.CharField(max_length=40, unique=True)
    visitor = models.CharField(_("Посетитель"), max_length=40, db_index=True)
    started = models.DateTimeField(_("Начало"), auto_now_add=True, db_index=True)
    last_seen = models.DateTimeField(_("Последняя активность"), auto_now=True)
    active_seconds = models.PositiveIntegerField(_("Время на сайте, сек"), default=0)
    ip = models.GenericIPAddressField(_("IP"), null=True, blank=True)
    user_agent = models.CharField(_("User-Agent"), max_length=400, blank=True)
    device = models.CharField(_("Устройство"), max_length=120, blank=True)
    device_type = models.CharField(_("Тип устройства"), max_length=10, blank=True, db_index=True)
    referrer = models.CharField(_("Откуда пришёл"), max_length=300, blank=True)
    source = models.CharField(_("Источник"), max_length=100, blank=True, db_index=True)
    landing = models.CharField(_("Страница входа"), max_length=300, blank=True)
    lang = models.CharField(_("Язык"), max_length=5, blank=True)
    screen = models.CharField(_("Экран"), max_length=20, blank=True)
    pageviews = models.PositiveIntegerField(_("Просмотры"), default=1)
    events = models.JSONField(_("События"), default=dict, blank=True)
    is_staff = models.BooleanField(_("Администратор"), default=False, db_index=True)

    class Meta:
        verbose_name, verbose_name_plural = _("Визит"), _("Визиты")
        ordering = ["-started"]

    def __str__(self):
        return f"{self.ip} {self.device} {self.started:%d.%m %H:%M}"

    @property
    def duration_label(self):
        m, sec = divmod(self.active_seconds, 60)
        return f"{m}:{sec:02d}"


class TelegramAccount(models.Model):
    """Link between an admin user and a Telegram account (login via the Telegram widget)."""
    user = models.OneToOneField(dj_settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram",
                                verbose_name=_("Пользователь"))
    telegram_id = models.BigIntegerField(_("Telegram ID"), unique=True)
    username = models.CharField(_("Username"), max_length=64, blank=True)
    first_name = models.CharField(_("Имя в Telegram"), max_length=128, blank=True)
    linked = models.DateTimeField(_("Привязан"), auto_now_add=True)
    last_login = models.DateTimeField(_("Последний вход"), null=True, blank=True)

    class Meta:
        verbose_name, verbose_name_plural = _("Telegram-аккаунт"), _("Telegram-аккаунты")

    def __str__(self):
        return f"@{self.username or self.telegram_id} → {self.user}"


# --- any content change drops the cached pages; deleted files are removed from disk
def page_cache_key(lang):
    return f"page:index:{lang}"


def _invalidate(sender, **kw):
    cache.delete_many([page_cache_key(code) for code in ("ru", "en")])


def _cleanup_files(sender, instance, **kw):
    for f in instance._meta.get_fields():
        if isinstance(f, models.ImageField):
            file = getattr(instance, f.name)
            if file:
                remove_variants(file.path)
                file.storage.delete(file.name)


for model in (SiteSettings, Category, Product, ProductImage, Review, InfoCard, Faq):
    post_save.connect(_invalidate, sender=model)
    post_delete.connect(_invalidate, sender=model)
for model in (ProductImage, Review):
    post_delete.connect(_cleanup_files, sender=model)

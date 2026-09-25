from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.urls import include, path, re_path
from django.utils.translation import gettext_lazy as _
from django.views.static import serve

from shop import admin as shop_admin
from shop import analytics, views

admin.site.site_header = _("GLÜCKSTAL — управление сайтом")
admin.site.site_title = "GLÜCKSTAL"
admin.site.index_title = _("Контент и настройки")
admin.site.site_url = "/"
admin.site.index_template = "admin/gl_index.html"
admin.site.login_template = "admin/gl_login.html"

urlpatterns = [
    path("robots.txt", views.robots),
    path("sitemap.xml", views.sitemap),
    path("api/order", views.order),
    path("api/health", views.health),
    path("api/track", analytics.track),
    path("i18n/", include("django.conf.urls.i18n")),  # переключение языка (в т.ч. в админке)
    path("admin/telegram/login/", shop_admin.telegram_login, name="telegram_login"),
    path("admin/telegram/link/", shop_admin.telegram_link, name="telegram_link"),
    path("admin/telegram/unlink/", shop_admin.telegram_unlink, name="telegram_unlink"),
    path("admin/", admin.site.urls),
    # загруженные в админке фото (том /data/media)
    re_path(r"^media/(?P<path>.+)$", lambda request, path: serve(request, path, document_root=settings.MEDIA_ROOT)),
]
# публичные страницы: / — русский, /en/ — английский
urlpatterns += i18n_patterns(
    path("", views.index, name="index"),
    path("privacy.html", views.privacy, name="privacy"),
    prefix_default_language=False,
)

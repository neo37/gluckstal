from django import template

register = template.Library()


@register.filter
def tr(obj, field):
    """{{ product|tr:"name" }} — English value on the English site when filled, otherwise the Russian one."""
    return obj.tr(field) if obj is not None else ""


@register.simple_tag
def gl_dashboard():
    from shop.dashboard import stats
    return stats()


@register.simple_tag
def gl_settings():
    from shop.models import SiteSettings
    return SiteSettings.load()

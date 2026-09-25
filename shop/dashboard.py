"""Numbers for the admin dashboard: visits, time on site, orders, clicks, sources, devices."""
import datetime
from collections import Counter

from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Order, Visit


def _period(since):
    visits = Visit.objects.filter(started__gte=since, is_staff=False)
    orders = Order.objects.filter(created__gte=since)
    v = visits.aggregate(n=Count("id"), people=Count("visitor", distinct=True),
                         avg=Avg("active_seconds", filter=Q(active_seconds__gt=0)))
    events = Counter()
    for ev in visits.values_list("events", flat=True):
        events.update(ev or {})
    n_orders = orders.count()
    return {
        "visits": v["n"], "visitors": v["people"], "avg_time": _mmss(v["avg"] or 0), "orders": n_orders,
        "conversion": f"{n_orders / v['n'] * 100:.1f}%" if v["n"] else "—",
        "tg": events.get("tg_click", 0), "wa": events.get("wa_click", 0), "products": events.get("product_open", 0),
    }


def _mmss(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def stats(days=30):
    now = timezone.localtime()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - datetime.timedelta(days=days - 1)
    visits = Visit.objects.filter(started__gte=start, is_staff=False)

    per_day_v = dict(visits.annotate(d=TruncDate("started")).values_list("d").annotate(n=Count("id")))
    per_day_o = dict(Order.objects.filter(created__gte=start).annotate(d=TruncDate("created"))
                     .values_list("d").annotate(n=Count("id")))
    chart = []
    for i in range(days):
        d = (start + datetime.timedelta(days=i)).date()
        chart.append({"date": d, "visits": per_day_v.get(d, 0), "orders": per_day_o.get(d, 0)})
    peak = max([c["visits"] for c in chart] + [1])
    for c in chart:
        c["h"] = round(c["visits"] / peak * 100)
        c["oh"] = min(100, round(c["orders"] / peak * 100)) if c["orders"] else 0

    return {
        "today": _period(today),
        "week": _period(today - datetime.timedelta(days=6)),
        "month": _period(start),
        "chart": chart,
        "sources": visits.values("source").annotate(n=Count("id")).order_by("-n")[:8],
        "devices": visits.values("device_type").annotate(n=Count("id")).order_by("-n"),
        "recent_visits": Visit.objects.filter(is_staff=False).prefetch_related("orders")[:25],
        "recent_orders": Order.objects.all()[:10],
        "new_orders": Order.objects.filter(status="new").count(),
        "failed_tg": Order.objects.filter(telegram="failed", status="new").count(),
    }

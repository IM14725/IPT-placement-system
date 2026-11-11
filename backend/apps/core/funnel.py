"""First-party signup funnel counters: how many people reach each step, with no cookies and no IPs.

Each step is a per-day Redis counter (``incr_daily``), so there is no table, no tracking identifier
and nothing personal stored. Because nobody is identified, the numbers are *page views*, not unique
visitors: someone who reloads the page counts again. Known crawlers are skipped. Everything fails open
when Redis is down.
"""

import re

from apps.core.cache import daily_series, incr_daily

# (key, label) in funnel order. The labels are shown to administrators on the Metrics page.
STEPS = (
    ("home", "Welcome page views"),
    ("register_view", "Register page views"),
    ("register_step2", "Reached details step"),
    ("register_step3", "Reached password step"),
    ("register_submit", "Submitted the form"),
    ("account_created", "Accounts created"),
)
STEP_KEYS = frozenset(key for key, _ in STEPS)
# Steps the browser may report itself (the wizard runs in the page); the rest are counted server-side.
BROWSER_STEPS = frozenset({"register_step2", "register_step3"})

_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|preview|monitor|uptime|curl|wget|python-requests|httpclient|headless|facebookexternalhit",
    re.I,
)


def is_bot(request):
    return bool(_BOT_RE.search(request.META.get("HTTP_USER_AGENT", "")))


def track(step, request=None):
    """Count one event for ``step``. Unknown steps and known crawlers are ignored."""
    if step not in STEP_KEYS:
        return
    if request is not None and is_bot(request):
        return
    incr_daily(f"funnel:{step}")


def funnel_summary(days=30):
    """Totals per step over the last ``days`` days, with conversion from the previous and first step."""
    rows = []
    first = previous = None
    for key, label in STEPS:
        series = daily_series(f"funnel:{key}", days=days)
        total = sum(point["value"] for point in series)
        if first is None:
            first = total
        rows.append(
            {
                "key": key,
                "label": label,
                "total": total,
                "of_previous": round(100 * total / previous, 1) if previous else None,
                "of_first": round(100 * total / first, 1) if first else None,
                "series": series,
            }
        )
        previous = total
    return rows

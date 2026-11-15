from django import template

from apps.core.timeleft import time_left_text

register = template.Library()


@register.filter
def time_left(deadline):
    """``{{ slot.application_deadline|time_left }}`` -> "36 hours left" / "25 minutes left"."""
    return time_left_text(deadline)

import re

from django import template

register = template.Library()


def _national(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("255"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return digits[:9]


@register.filter
def phone_local(value):
    """``'255791099136'`` -> ``'791 099 136'``: the number without the country code (a "+255" prefix is shown beside it)."""
    national = _national(value)
    return re.sub(r"(\d{3})(?=\d)", r"\1 ", national).strip()


@register.filter
def phone_intl(value):
    """``'255700000001'`` -> ``'+255 700 000 001'`` for display; anything that is not a Tanzanian number is returned unchanged."""
    national = _national(value)
    if len(national) != 9:
        return value
    return "+255 " + phone_local(value)

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def money(value):
    """``Decimal('300000.00')`` -> ``'300,000'``: whole shillings with a thousands separator, no decimals."""
    try:
        return f"{Decimal(str(value)):,.0f}"
    except (InvalidOperation, ValueError, TypeError):
        return value

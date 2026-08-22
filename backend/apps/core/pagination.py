"""Bounded server-side pagination for list views.

Keeps heavy list queries to a single page worth of rows (LIMIT/OFFSET) instead
of loading the whole table into memory and rendering it on every request,
which is what causes DB + server timeouts as lists grow.
"""

from django.core.paginator import Paginator

DEFAULT_PAGE_SIZE = 20


def paginate(queryset, page_number, page_size=DEFAULT_PAGE_SIZE):
    """Return a Page object with a hard cap on page size."""
    try:
        page_number = max(1, int(page_number))
    except (TypeError, ValueError):
        page_number = 1
    return Paginator(queryset, page_size).get_page(page_number)
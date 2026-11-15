from django import template

from apps.core.thumbnails import thumbnail_url

register = template.Library()


@register.filter
def thumb(fieldfile, size=96):
    """``{{ profile.profile_photo|thumb:96 }}`` -> URL of a small square WebP copy (falls back to the original)."""
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 96
    return thumbnail_url(fieldfile, size)

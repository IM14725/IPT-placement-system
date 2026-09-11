from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def is_active(context, *url_names):
    """Return 'active' if current URL matches any of the given url_names."""
    request = context.get("request")
    if not request or not hasattr(request, "resolver_match") or not request.resolver_match:
        return ""
    current_name = request.resolver_match.url_name
    if current_name in url_names:
        return "active"
    return ""
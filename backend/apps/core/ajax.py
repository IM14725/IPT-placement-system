"""Let the form-loading script follow a redirect with ONE page load instead of two.

``form-loading.js`` posts forms with fetch(). fetch() silently follows the 302, so the destination
page (e.g. the dashboard) was rendered for that fetch and then rendered *again* when the script set
``window.location``. This middleware answers such POSTs with ``200`` plus ``X-Redirect-To`` instead, and
the script navigates once. Cookies (session, messages) set by the response are kept.
"""

from django.http import HttpResponse


class AjaxRedirectMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            request.method == "POST"
            and request.headers.get("X-Requested-With") == "XMLHttpRequest"
            and 300 <= response.status_code < 400
            and response.has_header("Location")
        ):
            location = response["Location"]
            response.status_code = 200
            response.content = b""
            response["X-Redirect-To"] = location
            del response["Location"]
            response["Cache-Control"] = "no-store"
        return response

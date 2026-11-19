"""The Content-Security-Policy forbids inline script handlers, so this guards the templates against them.

Admin-console pages cannot all be browsed in a test, so the static scan is what keeps *every* template
(including those) compatible with the policy.
"""

import re
from pathlib import Path

import pytest
from django.test import Client

BACKEND = Path(__file__).resolve().parents[2]
TEMPLATES = BACKEND / "templates"
STATIC_JS = BACKEND / "static" / "js"

INLINE_HANDLER = re.compile(r"""\son[a-z]+\s*=\s*["']""", re.IGNORECASE)
JAVASCRIPT_URL = re.compile(r"""(?:href|src|action|formaction)\s*=\s*["']\s*javascript:""", re.IGNORECASE)
SCRIPT_OPEN = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)


def _templates():
    return sorted(TEMPLATES.rglob("*.html"))


def _rel(path):
    return path.relative_to(TEMPLATES).as_posix()


def test_no_template_uses_an_inline_event_handler_or_javascript_url():
    offenders = []
    for path in _templates():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if INLINE_HANDLER.search(line) or JAVASCRIPT_URL.search(line):
                offenders.append(f"{_rel(path)}:{number}: {line.strip()[:120]}")
    assert not offenders, (
        "Inline on*= handlers and javascript: URLs are blocked by the CSP. Use the data-* actions in "
        "static/js/csp-actions.js or an addEventListener in a nonce'd script:\n" + "\n".join(offenders)
    )


def test_every_inline_script_carries_the_csp_nonce():
    offenders = []
    for path in _templates():
        text = path.read_text(encoding="utf-8")
        for match in SCRIPT_OPEN.finditer(text):
            attrs = match.group(1)
            if re.search(r"\bsrc\s*=", attrs):
                continue  # external file from our own origin: allowed by 'self'
            if re.search(r"""type\s*=\s*["']application/(?:ld\+)?json["']""", attrs):
                continue  # data block, never executed
            if 'nonce="{{ csp_nonce }}"' not in attrs:
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{_rel(path)}:{line}: {match.group(0)}")
    assert not offenders, (
        'Inline <script> blocks need nonce="{{ csp_nonce }}" or the CSP refuses to run them:\n'
        + "\n".join(offenders)
    )


def test_static_scripts_do_not_build_inline_handlers():
    offenders = []
    for path in sorted(STATIC_JS.glob("*.js")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""\bon(?:click|change|submit|input|load|error|focus|blur|key[a-z]+|mouse[a-z]+)\s*=\s*\\?["']""", line):
                offenders.append(f"{path.name}:{number}: {line.strip()[:120]}")
            if JAVASCRIPT_URL.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()[:120]}")
    assert not offenders, "HTML built in JS must not contain inline handlers:\n" + "\n".join(offenders)


@pytest.mark.django_db
def test_policy_has_no_unsafe_inline_for_scripts_and_its_nonce_matches_the_page():
    resp = Client().get("/auth/login/")
    policy = resp["Content-Security-Policy"]
    script_src = next(d for d in policy.split(";") if d.strip().startswith("script-src")).strip()
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
    nonce = re.search(r"'nonce-([^']+)'", script_src).group(1)
    page = resp.content.decode()
    inline_nonces = set(re.findall(r'<script nonce="([^"]+)"', page))
    assert inline_nonces == {nonce}, "every inline script must use this response's nonce"
    assert "frame-ancestors 'none'" in policy and "object-src 'none'" in policy
    assert policy.count("script-src") == 1  # one policy, not two merged definitions


@pytest.mark.django_db
def test_each_response_gets_a_fresh_nonce():
    a = Client().get("/auth/login/")["Content-Security-Policy"]
    b = Client().get("/auth/login/")["Content-Security-Policy"]
    assert re.search(r"'nonce-([^']+)'", a).group(1) != re.search(r"'nonce-([^']+)'", b).group(1)


@pytest.mark.django_db
def test_trace_and_track_are_refused():
    for method in ("TRACE", "TRACK"):
        resp = Client().generic(method, "/")
        assert resp.status_code == 405


@pytest.mark.django_db
def test_public_health_probe_reveals_status_only():
    resp = Client().get("/platform/")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


@pytest.mark.django_db
def test_every_page_loads_the_keyboard_script_and_a_keyboard_aware_viewport():
    page = Client().get("/auth/login/").content.decode()
    assert "js/keyboard.js" in page
    assert "interactive-widget=resizes-content" in page
    assert (STATIC_JS / "keyboard.js").exists()

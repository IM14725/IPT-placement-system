"""Kiswahili coverage guard.

Every string wrapped for translation in a public / student / organization template or Python module must
have a Kiswahili entry in locale/sw (see scripts/build_translations.py). The admin console (the
/platform/ pages and admin MFA screens) intentionally stays English and is skipped here.
"""
import ast
import gettext
import re
from pathlib import Path

import pytest
from django.test import Client
from django.utils.translation.template import templatize

BACKEND = Path(__file__).resolve().parents[2]
MO_PATH = BACKEND / "locale" / "sw" / "LC_MESSAGES" / "django.mo"

ADMIN_TEMPLATES = {
    "core/admin_detail.html", "core/admin_form.html", "core/admin_list.html", "core/admin_profile_edit.html",
    "core/admin_settings.html", "core/admin_slots.html", "core/audit_detail.html", "core/audit_logs.html",
    "core/break_glass.html", "core/directory.html", "core/integrity.html", "core/ledger.html",
    "core/locations.html", "core/metrics.html", "core/notification_templates.html", "core/one_time_secret.html",
    "core/organization_verification.html", "core/recovery_queue.html", "core/reports.html",
    "core/role_management.html", "core/student_verification.html", "core/universities.html",
    "core/verification_queue.html",
    "includes/account_status.html",
    "registration/admin_recovery_request.html", "registration/mfa_challenge.html",
    "registration/mfa_recovery_codes.html", "registration/mfa_settings.html", "registration/mfa_step_up.html",
    "registration/mfa_totp_setup.html",
}
ADMIN_PYTHON = (
    "apps/core/views.py", "apps/core/views_admins.py", "apps/core/views_governance.py", "apps/core/metrics.py",
    "apps/core/admin", "apps/core/permissions.py", "apps/core/urls.py",
    "apps/accounts/views_mfa.py", "apps/accounts/mfa.py", "apps/accounts/recovery.py", "apps/accounts/breakglass.py",
    "apps/accounts/admins.py", "apps/locations/views_admin.py",
)
GETTEXT_CALL = re.compile(r"\b(gettext|ngettext)\(")


@pytest.fixture(scope="module")
def catalog():
    translations = gettext.GNUTranslations(open(MO_PATH, "rb"))
    return translations._catalog


def _balanced(text, start):
    depth, i, quote = 0, start, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return None


def _template_msgids():
    found = {}
    for path in sorted((BACKEND / "templates").rglob("*.html")):
        rel = path.relative_to(BACKEND / "templates").as_posix()
        if rel in ADMIN_TEMPLATES:
            continue
        text = templatize(path.read_text(encoding="utf-8"), origin=rel)
        for m in GETTEXT_CALL.finditer(text):
            args = _balanced(text, m.end() - 1)
            if args is None:
                continue
            try:
                node = ast.parse("f(" + re.sub(r"(?<![\w])u(['\"])", lambda x: x.group(1), args) + ")", mode="eval").body
            except SyntaxError:
                continue
            vals = [a.value for a in node.args if isinstance(a, ast.Constant)]
            if m.group(1) == "gettext" and vals and isinstance(vals[0], str):
                found.setdefault(vals[0], set()).add(rel)
            elif m.group(1) == "ngettext" and len(vals) >= 2:
                found.setdefault((vals[0], 0), set()).add(rel)
    return found


def _python_msgids():
    names = {"_", "gettext", "gettext_lazy", "ugettext", "ugettext_lazy"}
    found = {}
    for path in sorted((BACKEND / "apps").rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if "/migrations/" in rel or "/test" in rel or path.name.startswith("test") or path.name == "admin.py":
            continue
        if any(rel.startswith(a) for a in ADMIN_PYTHON):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            first = node.args[0]
            if name in names and isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.setdefault(first.value, set()).add(rel)
            elif name == "ngettext" and isinstance(first, ast.Constant):
                found.setdefault((first.value, 0), set()).add(rel)
    return found


def test_every_translatable_template_string_has_a_swahili_entry(catalog):
    missing = {k: sorted(v)[:2] for k, v in _template_msgids().items() if k not in catalog}
    assert not missing, (
        "Add these to backend/scripts/sw/ui.json (or plural.json) and run scripts/build_translations.py: "
        + repr(list(missing.items())[:15])
    )


def test_every_translatable_python_string_has_a_swahili_entry(catalog):
    missing = {k: sorted(v)[:2] for k, v in _python_msgids().items() if k not in catalog}
    assert not missing, (
        "Add these to backend/scripts/sw/ui.json (or plural.json) and run scripts/build_translations.py: "
        + repr(list(missing.items())[:15])
    )


def test_catalog_keeps_the_original_hand_written_entries(catalog):
    # The .po is the base of the build script; losing entries would silently revert pages to English.
    assert len(catalog) > 1400
    assert catalog["Login"] == "Ingia"
    assert catalog["Dashboard"] == "Dashibodi"


@pytest.mark.django_db
def test_pages_render_in_kiswahili_with_the_language_cookie():
    c = Client()
    c.cookies["django_language"] = "sw"
    login = c.get("/auth/login/").content.decode("utf-8")
    assert "Umesahau nenosiri?" in login
    forgot = c.get("/auth/forgot-password/").content.decode("utf-8")
    assert "Umesahau nenosiri lako?" in forgot
    terms = c.get("/platform/legal/terms/").content.decode("utf-8")
    assert "Masharti" in terms and "tafsiri ya heshima" in terms  # courtesy-translation notice


@pytest.mark.django_db
def test_english_pages_have_no_courtesy_notice():
    c = Client()
    c.cookies["django_language"] = "en"
    terms = c.get("/platform/legal/terms/").content.decode("utf-8")
    assert "courtesy translation" not in terms
    assert "Terms &amp; Conditions" in terms or "Terms &" in terms


@pytest.mark.django_db
def test_form_labels_and_choice_labels_are_translated():
    from django.utils.translation import override

    from apps.notifications.models import NotificationType
    from apps.students.forms import StudentProfileForm

    with override("sw"):
        assert str(NotificationType.PAYMENT.label) == "Malipo"
        form = StudentProfileForm()
        assert str(form.fields["gender"].choices[0][1]) == "- Chagua chaguo -"

"""Every outgoing email carries a branded HTML part (logo, colours, OTP code box) next to the plain text."""
from django.core import mail

from apps.core import email_layout, providers


def _html(message):
    return next(content for content, mime in message.alternatives if mime == "text/html")


def test_otp_email_has_plain_text_unchanged_and_a_branded_html_part():
    text = "Your IPT Marketplace verification code is 482913. It expires in 5 minutes."
    providers.send_email("Your IPT Marketplace verification code", ["a@x.tz"], text)
    m = mail.outbox[-1]
    assert m.body == text  # plain text untouched: code parsers and the text fallback still work
    page = _html(m)
    assert ">482913<" in page and "user-select:all" in page and "It expires in 5 minutes." in page
    assert "#3525CD" in page and "cid:ipt-logo" in page and "never share this code" in page
    # the logo travels inline, inside the HTML part (multipart/related), not as a downloadable attachment
    raw = m.message()
    assert raw.get_content_type() == "multipart/alternative"
    related = raw.get_body(preferencelist=("related",)) or raw.get_payload()[-1]
    assert related.get_content_type() == "multipart/related"
    logo = related.get_payload()[-1]
    assert logo["Content-ID"] == "<ipt-logo>" and logo.get_content_type() == "image/png"
    assert logo.get_content_disposition() == "inline" and logo.get_filename() is None  # no attachment chip in Gmail


def test_text_is_escaped_and_only_http_urls_become_links():
    providers.send_email(
        "Hello <b>x</b>", ["a@x.tz"],
        'Hi <script>alert(1)</script>\n\nSee https://example.tz/a?b=1&c=2. Also javascript:alert(1)',
    )
    page = _html(mail.outbox[-1])
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert 'href="https://example.tz/a?b=1&amp;c=2"' in page
    assert 'href="javascript:' not in page


def test_labelled_url_becomes_a_button_and_key_value_lines_a_details_table():
    providers.send_email("Welcome", ["a@x.tz"], "Log in: https://iptmarketplace.tz/auth/login/")
    assert 'bgcolor="#3525CD"' in _html(mail.outbox[-1]) and ">Log in<" in _html(mail.outbox[-1])
    providers.send_email("Account", ["a@x.tz"], "Sign in: https://x.tz/login\nEmail: boss@x.tz\nTemporary password: Ab-12_xyz")
    page = _html(mail.outbox[-1])
    assert "Temporary password" in page and "Ab-12_xyz" in page and "Consolas" in page


def test_html_can_be_switched_off_and_a_layout_error_still_sends_plain_text(monkeypatch):
    providers.send_email("Plain", ["a@x.tz"], "just text", html=False)
    assert mail.outbox[-1].alternatives == []

    def boom(*a, **k):
        raise RuntimeError("template broke")

    monkeypatch.setattr(email_layout, "render_email_html", boom)
    providers.send_email("Still sent", ["a@x.tz"], "hello")
    m = mail.outbox[-1]
    assert m.body == "hello" and m.alternatives == [] and m.logo is None


def test_test_prefix_is_not_shown_in_the_heading():
    assert "<h1" in email_layout.render_email_html("[TEST] Reminder", "x") and ">[TEST]" not in email_layout.render_email_html("[TEST] Reminder", "x")


def test_hosted_logo_url_replaces_the_embedded_image(settings):
    settings.EMAIL_LOGO_URL = "https://cdn.example.tz/logo.png"
    providers.send_email("Welcome", ["a@x.tz"], "hello")
    m = mail.outbox[-1]
    page = _html(m)
    assert 'src="https://cdn.example.tz/logo.png"' in page and "cid:ipt-logo" not in page
    assert "ipt-logo" not in str(m.message())  # nothing embedded, nothing attached

    settings.EMAIL_LOGO_URL = "http://insecure.example.tz/logo.png"  # only https is used
    providers.send_email("Welcome", ["a@x.tz"], "hello")
    assert "insecure.example.tz" not in _html(mail.outbox[-1])


def test_every_email_footer_links_to_the_platform_social_profiles():
    for subject, body in (("Welcome", "hello"), ("OTP", "Your IPT Marketplace verification code is 123456. It expires in 5 minutes.")):
        providers.send_email(subject, ["a@x.tz"], body)
        page = _html(mail.outbox[-1])
        for name, url, _color in email_layout.SOCIAL_LINKS:
            assert f'href="{url}"' in page and f">{name}<" in page
        assert "Follow us" in page


def test_social_links_use_hosted_icons_next_to_the_hosted_logo(settings):
    settings.EMAIL_LOGO_URL = "https://cdn.example.tz/static/images/email-logo.png"
    providers.send_email("Welcome", ["a@x.tz"], "hello")
    page = _html(mail.outbox[-1])
    for name, url, _color in email_layout.SOCIAL_LINKS:
        assert f'href="{url}"' in page
        assert f'src="https://cdn.example.tz/static/images/email-icon-{name.lower()}.png"' in page
        assert f'alt="{name}"' in page
    assert ">Instagram<" not in page  # icons replace the text buttons


def test_each_email_ends_with_its_own_invisible_token_so_gmail_does_not_trim_it():
    import re

    providers.send_email("Same subject", ["a@x.tz"], "same body")
    providers.send_email("Same subject", ["a@x.tz"], "same body")
    first, second = (_html(m) for m in mail.outbox[-2:])
    tail = lambda page: re.search(r'text-align:center;">([0-9a-f]{12})</div>\s*</body>', page).group(1)
    assert tail(first) != tail(second)


def test_icon_files_exist_for_every_social_link():
    from pathlib import Path

    for name, _url, _color in email_layout.SOCIAL_LINKS:
        assert (Path(email_layout.LOGO_PATH).parent / f"email-icon-{name.lower()}.png").exists()


def test_tone_adds_a_coloured_label_and_action_a_button_for_plain_text_emails():
    providers.send_email(
        "[URGENT] Break-glass emergency account was used", ["a@x.tz"], "The emergency account just signed in.",
        tone="alert", action=("Review the audit log", "https://x.tz/platform/audit-logs/"),
    )
    page = _html(mail.outbox[-1])
    assert "Security alert" in page and "#B3261E" in page
    assert ">Review the audit log<" in page and 'href="https://x.tz/platform/audit-logs/"' in page
    assert mail.outbox[-1].body == "The emergency account just signed in."  # the text part is unchanged


def test_an_action_that_is_not_an_http_link_is_ignored():
    page = email_layout.render_email_html("S", "hello", tone="nonsense", action=("Go", "javascript:alert(1)"))
    assert "javascript:" not in page and "Security alert" not in page


def test_security_emails_use_the_designed_layouts(db, settings):
    from apps.accounts import recovery
    from apps.core.email_layout import site_link

    assert site_link("login").startswith(settings.SITE_URL) and site_link("platform-recovery").endswith("/recovery/")
    assert recovery.site_link is site_link

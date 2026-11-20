"""Branded HTML layout for every outgoing email (logo, platform colours, OTP code box, buttons).

Callers keep passing plain text to ``providers.send_email``. This module turns that text into a table-based
HTML email (works in Gmail, Outlook and phone clients) and the plain text stays as the fallback part, so
nothing that reads ``message.body`` changes. All text is HTML-escaped; only http(s) URLs become links.
Colours are the platform tokens from ``tailwind.config.js`` / ``theme.css``.
"""
from __future__ import annotations

import re
import uuid
from datetime import date
from html import escape
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string

LOGO_CID = "ipt-logo"
LOGO_PATH = Path(settings.BASE_DIR) / "static" / "images" / "email-logo.png"

# Platform palette (rgb triplets in theme.css -> hex).
PRIMARY = "#3525CD"
PRIMARY_CONTAINER = "#4F46E5"
INK = "#191C1D"
MUTED = "#464555"
SOFT = "#6B6A7B"
LINE = "#E3E2EC"
TINT = "#EEF0FF"
PAGE_BG = "#F4F5F7"
ACCENT = "#F59A23"  # the orange in the logo

# The same profiles as the site footer ("Follow Us" in templates/base.html), without the tracking parameters.
SOCIAL_LINKS = (
    ("Instagram", "https://www.instagram.com/semauya.tech", "#E1306C"),
    ("TikTok", "https://www.tiktok.com/@semauya.tech", "#111111"),
    ("Facebook", "https://www.facebook.com/share/1Z9PivTbuM/", "#1877F2"),
)

_URL = re.compile(r"https?://[^\s<>\"']+")
_OTP = re.compile(r"verification code is\s+(\d{4,8})\.?\s*(.*)", re.I | re.S)
_DETAIL = re.compile(r"^([A-Z][\w ]{1,28}):\s+(\S.*)$")
_LABELLED_URL = re.compile(r"^(.{1,60}?):\s*(https?://\S+)$")


def _link(url: str) -> str:
    trail = ""
    while url and url[-1] in ".,;:!?)":
        trail = url[-1] + trail
        url = url[:-1]
    safe = escape(url, quote=True)
    return f'<a href="{safe}" style="color:{PRIMARY};text-decoration:underline;word-break:break-all;">{safe}</a>{escape(trail)}'


def _inline(text: str) -> str:
    """Escape ``text`` and turn bare http(s) URLs into links."""
    out, last = [], 0
    for m in _URL.finditer(text):
        out.append(escape(text[last:m.start()]))
        out.append(_link(m.group(0)))
        last = m.end()
    out.append(escape(text[last:]))
    return "".join(out).replace("\n", "<br>")


def _button(label: str, url: str) -> str:
    safe = escape(url, quote=True)
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 20px;"><tr>'
        f'<td align="center" bgcolor="{PRIMARY}" style="border-radius:8px;">'
        f'<a href="{safe}" style="display:inline-block;padding:14px 28px;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:16px;font-weight:bold;color:#FFFFFF;text-decoration:none;border-radius:8px;">{escape(label)}</a>'
        "</td></tr></table>"
    )


def _details(lines: list[str]) -> str:
    rows = []
    for line in lines:
        key, value = _DETAIL.match(line).groups()
        shown = _link(value) if _URL.fullmatch(value) else (
            f'<span style="font-family:Consolas,Menlo,monospace;font-size:15px;">{escape(value)}</span>'
        )
        rows.append(
            f'<tr><td style="padding:8px 12px;font-size:14px;color:{SOFT};white-space:nowrap;vertical-align:top;">{escape(key)}</td>'
            f'<td style="padding:8px 12px;font-size:15px;color:{INK};word-break:break-all;">{shown}</td></tr>'
        )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{TINT}" '
        f'style="background:{TINT};border:1px solid {LINE};border-radius:10px;margin:4px 0 20px;">{"".join(rows)}</table>'
    )


def _otp_box(code: str, note: str) -> str:
    note_html = f'<div style="font-size:14px;color:{MUTED};margin-top:10px;">{escape(note.strip())}</div>' if note.strip() else ""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{TINT}" '
        f'style="background:{TINT};border:1px solid {LINE};border-radius:12px;margin:8px 0 18px;"><tr>'
        f'<td align="center" style="padding:22px 12px;">'
        f'<div style="font-size:13px;letter-spacing:1px;text-transform:uppercase;color:{SOFT};margin-bottom:8px;">Your verification code</div>'
        f'<div style="font-family:Consolas,Menlo,monospace;font-size:36px;font-weight:bold;letter-spacing:2px;color:{PRIMARY};'
        f'-webkit-user-select:all;user-select:all;">{escape(code)}</div>'
        f"{note_html}</td></tr></table>"
        f'<p style="margin:0 0 16px;font-size:14px;line-height:1.6;color:{MUTED};">'
        "For your security, never share this code with anyone. IPT Marketplace staff will never ask for it.</p>"
    )


# tone -> (label, text colour, background)
TONES = {
    "alert": ("Security alert", "#B3261E", "#FDECEC"),
    "warning": ("Action needed", "#A16207", "#FFF8EC"),
    "success": ("Completed", "#1B7F4B", "#E8F6EE"),
    "info": ("Good to know", PRIMARY, TINT),
}


def _tone_box(tone: str) -> str:
    label, color, background = TONES[tone]
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 18px;"><tr>'
        f'<td bgcolor="{background}" style="background:{background};border-left:4px solid {color};border-radius:6px;'
        f'padding:10px 14px;font-size:13px;font-weight:bold;letter-spacing:1px;text-transform:uppercase;color:{color};">'
        f"{escape(label)}</td></tr></table>"
    )


def build_blocks(body: str) -> tuple[str, str]:
    """Return ``(content_html, preheader)`` for the plain-text ``body``."""
    text = (body or "").replace("\r\n", "\n").strip()
    preheader = re.sub(r"\s+", " ", text)[:110]
    m = _OTP.search(text)
    if m:
        code, note = m.group(1), m.group(2)
        intro = "Use the code below to continue. It was requested for your IPT Marketplace account."
        paragraph = f'<p style="margin:0 0 16px;font-size:16px;line-height:1.6;color:{INK};">{escape(intro)}</p>'
        return paragraph + _otp_box(code, note), f"Your IPT Marketplace verification code is {code}"
    parts = []
    for para in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        if not lines:
            continue
        if len(lines) == 1 and _LABELLED_URL.match(lines[0]) and len(_LABELLED_URL.match(lines[0]).group(1).split()) <= 4:
            label, url = _LABELLED_URL.match(lines[0]).groups()
            parts.append(_button(label, url))
        elif len(lines) >= 2 and all(_DETAIL.match(ln) for ln in lines):
            parts.append(_details(lines))
        else:
            parts.append(f'<p style="margin:0 0 16px;font-size:16px;line-height:1.6;color:{INK};">{_inline(chr(10).join(lines))}</p>')
    return "".join(parts), preheader


def render_email_html(
    subject: str,
    body: str,
    *,
    content_html: str | None = None,
    preheader: str | None = None,
    heading: str | None = None,
    tone: str | None = None,
    action: tuple[str, str] | None = None,
) -> str:
    """The branded page. ``content_html`` (trusted markup built by our own code, already escaped) replaces
    the blocks normally worked out from the plain-text ``body``: used by the receipt and acceptance emails.

    ``tone`` (``alert``, ``warning``, ``success`` or ``info``) puts a coloured label box above the text, and
    ``action`` is a ``(label, https url)`` button added below it. Both are optional design hints for plain
    text emails (security alerts, account notices); the text itself is unchanged."""
    heading = heading or re.sub(r"^\[TEST\]\s*", "", subject or "").strip() or "IPT Marketplace"
    if content_html is None:
        content, preheader = build_blocks(body)
        if tone in TONES:
            content = _tone_box(tone) + content
        if action and re.match(r"https?://", action[1] or ""):
            content += _button(action[0], action[1])
    else:
        content, preheader = content_html, preheader or ""
    return render_to_string(
        "email/branded.html",
        {
            "heading": heading,
            "content": content,
            "preheader": preheader,
            "logo_cid": LOGO_CID,
            "logo_url": _logo_url(),
            "has_logo": LOGO_PATH.exists(),
            "year": date.today().year,
            "social_links": [
                {"name": n, "url": u, "color": c, "icon": _icon_url(n)} for n, u, c in SOCIAL_LINKS
            ],
            # Different in every email and invisible: Gmail hides the ending an email shares with the previous
            # ones of a conversation behind a "show trimmed content" button; a unique last line prevents that.
            "nonce": uuid.uuid4().hex[:12],
            "c": {
                "primary": PRIMARY, "primary_container": PRIMARY_CONTAINER, "ink": INK, "muted": MUTED,
                "soft": SOFT, "line": LINE, "page": PAGE_BG, "accent": ACCENT,
            },
        },
    )


def _logo_url() -> str:
    url = (getattr(settings, "EMAIL_LOGO_URL", "") or "").strip()
    return url if url.startswith("https://") else ""


def _icon_url(name: str) -> str:
    """Hosted PNG next to the hosted logo (``email-icon-<name>.png``); empty means "use a text button"."""
    base = _logo_url()
    return f"{base.rsplit('/', 1)[0]}/email-icon-{name.lower()}.png" if base else ""


def logo_bytes() -> bytes | None:
    try:
        return LOGO_PATH.read_bytes()
    except OSError:
        return None


def site_link(url_name: str) -> str:
    """Absolute address of a named page, for buttons in emails that are built without a request."""
    from django.urls import reverse

    return f"{settings.SITE_URL}{reverse(url_name)}"

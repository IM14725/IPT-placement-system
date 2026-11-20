"""Branded HTML layout for every outgoing email (logo, platform colours, OTP code box, buttons).

Callers keep passing plain text to ``providers.send_email``. This module turns that text into a table-based
HTML email (works in Gmail, Outlook and phone clients) and the plain text stays as the fallback part, so
nothing that reads ``message.body`` changes. All text is HTML-escaped; only http(s) URLs become links.
Colours are the platform tokens from ``tailwind.config.js`` / ``theme.css``.
"""
from __future__ import annotations

import re
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


def render_email_html(subject: str, body: str) -> str:
    heading = re.sub(r"^\[TEST\]\s*", "", subject or "").strip() or "IPT Marketplace"
    content, preheader = build_blocks(body)
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
            "c": {
                "primary": PRIMARY, "primary_container": PRIMARY_CONTAINER, "ink": INK, "muted": MUTED,
                "soft": SOFT, "line": LINE, "page": PAGE_BG, "accent": ACCENT,
            },
        },
    )


def _logo_url() -> str:
    url = (getattr(settings, "EMAIL_LOGO_URL", "") or "").strip()
    return url if url.startswith("https://") else ""


def logo_bytes() -> bytes | None:
    try:
        return LOGO_PATH.read_bytes()
    except OSError:
        return None

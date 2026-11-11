"""Helpers for the platform's single input style (the icon-chip field).

``{% include "includes/form_field.html" with field=form.email %}`` draws label + chip box + help + errors.
``icon_for`` picks the chip icon from a field's name / type / placeholder; the same rules are used by the
one-off script that converted raw ``<input>`` tags, so new fields look the same as old ones.
"""

import re

from django import template

register = template.Library()

# first match wins: (pattern on "name placeholder id", icon)
_RULES = (
    (r"search|^q\b|\bq$|query|keyword", "search"),
    (r"e-?mail", "mail"),
    (r"pass(word)?", "lock"),
    (r"phone|mobile|tel\b", "call"),
    (r"otp|code|pin\b", "pin"),
    (r"date|_from|_to$|deadline|birth|expir", "calendar_today"),
    (r"region", "map"),
    (r"district|location", "location_on"),
    (r"ward|street|address", "home"),
    (r"organi[sz]ation|company|employer", "apartment"),
    (r"universit|institution|school|education|level|course|programme|program", "school"),
    (r"industry|department|field|sector", "work"),
    (r"amount|fee|price|stipend|cost|tzs", "payments"),
    (r"limit|number|count|max|min|capacity|seats|year", "tag"),
    (r"status", "flag"),
    (r"role|position|title|job", "badge"),
    (r"module|action|actor|target|type|kind|category|reason|note|message|detail|comment|description|body|text|report", "edit_note"),
    (r"first|last|name|full", "person"),
    (r"subject|label|key", "title"),
)


def icon_for(name="", placeholder="", input_type="", kind="input"):
    text = f"{name} {placeholder}".lower()
    if input_type == "password":
        return "lock"
    if input_type == "email":
        return "mail"
    if input_type in ("date", "datetime-local"):
        return "calendar_today"
    if input_type == "tel":
        return "call"
    if input_type == "number" and not re.search(r"fee|amount|price|stipend", text):
        return "tag"
    for pattern, icon in _RULES:
        if re.search(pattern, text):
            return icon
    if kind == "textarea":
        return "notes"
    if kind == "select":
        return "unfold_more"
    return "edit"


@register.filter
def field_icon(field):
    widget = field.field.widget
    return icon_for(
        field.name,
        widget.attrs.get("placeholder", ""),
        getattr(widget, "input_type", "") or "",
        "textarea" if widget.__class__.__name__ == "Textarea" else ("select" if "Select" in widget.__class__.__name__ else "input"),
    )


@register.filter
def widget_kind(field):
    """'checkbox', 'radio', 'file' or 'other': checkboxes/radios/files are not drawn as chip boxes."""
    widget = field.field.widget
    input_type = getattr(widget, "input_type", "")
    if input_type in ("checkbox", "radio", "file"):
        return input_type
    if widget.__class__.__name__ in ("CheckboxSelectMultiple", "RadioSelect", "ClearableFileInput", "FileInput"):
        return "file" if "File" in widget.__class__.__name__ else "radio"
    return "other"

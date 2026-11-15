r"""Build the Kiswahili catalog: locale/sw/LC_MESSAGES/django.po and django.mo (no GNU gettext needed).

The existing django.po is the base and is never shrunk: entries are only added or updated. New and
changed strings live in JSON files in scripts/sw/ (``ui.json`` and ``legal.json`` map the English
msgid to Kiswahili; ``plural.json`` lists plural forms). Workflow after adding a {% trans %} /
gettext string:

    1. add "English text": "Kiswahili" to scripts/sw/ui.json (keep %(name)s placeholders and HTML tags)
    2. backend\.venv\Scripts\python scripts\build_translations.py
    3. restart Django (the .mo is read at startup)

``apps/core/test_translations.py`` fails when a string in a (non-admin) template or Python module has no
Kiswahili entry, so a missing translation is caught before release. Needs ``polib`` (requirements-dev.txt).
Kiswahili text should be reviewed by a native speaker, in particular the legal pages.
"""
import json
from pathlib import Path

import polib

BASE = Path(__file__).resolve().parent.parent
LOCALE_DIR = BASE / "locale" / "sw" / "LC_MESSAGES"
PO_PATH = LOCALE_DIR / "django.po"
MO_PATH = LOCALE_DIR / "django.mo"
SW_DIR = Path(__file__).resolve().parent / "sw"


def upsert(po, index, msgid, msgstr, msgid_plural=None):
    entry = index.get((msgid, None))
    if entry is None:
        entry = polib.POEntry(msgid=msgid)
        po.append(entry)
        index[(msgid, None)] = entry
    if msgid_plural is not None:
        entry.msgid_plural = msgid_plural
        entry.msgstr_plural = {i: text for i, text in enumerate(msgstr)}
        entry.msgstr = ""
    else:
        entry.msgstr = msgstr
    if "fuzzy" in entry.flags:
        entry.flags.remove("fuzzy")


def main():
    po = polib.pofile(str(PO_PATH), wrapwidth=0)
    index = {(e.msgid, e.msgctxt): e for e in po}
    before = len(po)
    for path in sorted(SW_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for msgid, msgstr in data.items():
                upsert(po, index, msgid, msgstr)
        else:
            for item in data:
                upsert(po, index, item["msgid"], item["msgstr"], item["msgid_plural"])
    po.sort(key=lambda e: e.msgid)
    po.metadata["PO-Revision-Date"] = "2026-11-15 00:00+0300"
    po.save(str(PO_PATH))
    po.save_as_mofile(str(MO_PATH))
    print(f"Wrote {PO_PATH} and {MO_PATH}: {len(po)} entries ({len(po) - before} new)")


if __name__ == "__main__":
    main()

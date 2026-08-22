r"""Build the Kiswahili translation catalog for Django.

Generates locale/sw/LC_MESSAGES/django.po and compiles django.mo directly
(no GNU gettext required on Windows). Re-run after adding new {% trans %}
strings: add the msgid + Swahili here, then execute this script.

    backend\.venv\Scripts\python scripts\build_translations.py
"""

import struct
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOCALE_DIR = BASE / "locale" / "sw" / "LC_MESSAGES"

# English (msgid) -> Kiswahili (msgstr). Keep keys EXACTLY as written in
# {% trans "..." %} / gettext_lazy("...") calls across templates and Python.
CATALOG = {
    # --- base.html chrome / nav ---
    "Language": "Lugha",
    "Dashboard": "Dashibodi",
    "Find a Slot": "Tafuta Nafasi",
    "My Applications": "Maombi Yangu",
    "Slots": "Nafasi",
    "Queue": "Foleni",
    "Directory": "Orodha",
    "Ledger": "Daftari",
    "Metrics": "Vipimo",
    "Templates": "Templeti",
    "Audit": "Ukaguzi",
    "Roles": "Majukumu",
    "Integrity": "Uadilifu",
    "Settings": "Mipangilio",
    "View your profile": "Angalia wasifu wako",
    "Profile": "Wasifu",
    "Logout": "Toka",
    "Login": "Ingia",
    "Register": "Jisajili",
    "&copy; 2024 IPT Marketplace. All rights reserved.": "&copy; 2024 IPT Marketplace. Haki zote zimehifadhiwa.",
    # --- auth ---
    "Welcome back to your <span class=\"text-primary-fixed\">training journey.</span>": "Karibu tena katika <span class=\"text-primary-fixed\">safari yako ya mafunzo.</span>",
    "Verified students, verified companies": "Wanafunzi waliothibitishwa, kampuni zilizothibitishwa",
    "One-tap mobile money payments": "Malipo ya pesa mtandaooni kwa mbofyo mmoja",
    "Instant SMS & email updates": "Sasisho za papo hapo kwa SMS na barua pepe",
    "Welcome Back": "Karibu Tena",
    "Welcome back": "Karibu tena",
    "Log in to manage your IPT applications.": "Ingia ili kudhibiti maombi yako ya IPT.",
    "Sign in": "Ingia",
    "Email address": "Barua pepe",
    "Password": "Nenosiri",
    "Forgot password?": "Umesahau nenosiri?",
    "New to the marketplace?": "Mgeni sokoni?",
    "Create an account": "Fungua akaunti",
    "Create Account": "Fungua Akaunti",
    "Find IPT slots that match your field": "Tafuta nafasi za IPT zinazolingana na fani yako",
    "Post openings and manage applicants": "Tangaza nafasi na udhibiti waombaji",
    "Strict verification keeps it trustworthy": "Uthibitisho mkali unauhakikisha uaminifu",
    "Create your account": "Fungua akaunti yako",
    "Join the marketplace as a student or company.": "Jiunge na soko kama mwanafunzi au kampuni.",
    "I am a...": "Mimi ni...",
    "We'll send your login and receipts here.": "Tutakutumia taarifa zako za kuingia na malipo hapa.",
    "First name": "Jina la kwanza",
    "Last name": "Jina la mwisho",
    "Company name": "Jina la kampuni",
    "Use the registered trading name of your company.": "Tumia jina la biashara lililosajiliwa la kampuni yako.",
    "Phone number": "Namba ya simu",
    "Optional - use 2556 or 2557 followed by 8 digits, e.g. 255712345678.": "Si lazima - tumia 2556 au 2557 ikifuatiwa na tarakimu 8, mfano 255712345678.",
    "Use at least 8 characters with letters and numbers.": "Tumia herufi 8 au zaidi zenye herufi na tarakimu.",
    "Already registered?": "Umejisajili tayari?",
    "Confirm password": "Thibitisha nenosiri",
    "Join the marketplace for <span class=\"text-secondary-fixed\">real-world training.</span>": "Jiunge na soko la <span class=\"text-secondary-fixed\">mafunzo ya vitendo.</span>",
    # --- home ---
    "IPT Marketplace — Student & Company Matching": "Soko la IPT — Kuunganisha Wanafunzi na Kampuni",
    "Connect students with companies for <br class=\"hidden sm:block\"> <span class=\"text-primary\">Industrial Practical Training.</span>": "Unganisha wanafunzi na kampuni kwa <br class=\"hidden sm:block\"> <span class=\"text-primary\">Mafunzo ya Vitendo ya Viwanda.</span>",
    "A secure marketplace for students to find verified IPT slots and companies to manage talent efficiently.": "Soko salama linalowasaidia wanafunzi kupata nafasi za IPT zilizothibitishwa na kampuni kusimamia vipaji kwa ufanisi.",
    "Get Started": "Anza",
    "Platform Features": "Vipengele vya Jukwaa",
    "For Students": "Kwa Wanafunzi",
    "Verify your academic documents, browse available training slots, and secure your placement with one-tap mobile money.": "Thibitisha nyaraka zako za kielimu, vinjari nafasi za mafunzo, na uhakikishe uwekaji wako kwa malipo ya pesa mtandaooni.",
    "For Companies": "Kwa Kampuni",
    "Post verified training slots, review applications from pre-verified students, and manage communication via instant SMS alerts.": "Tangaza nafasi za mafunzo zilizothibitishwa, pitia maombi kutoka kwa wanafunzi waliothibitishwa, na udhibiti mawasiliano kwa SMS za papo hapo.",
    "Secure & Verified": "Salama & Thibitishwa",
    "Built on trust. We enforce strict document verification protocols for both students and companies to ensure a safe and professional ecosystem.": "Imejengwa juu ya uaminifu. Tunatekeleza utaratibu mkali wa uthibitisho wa nyaraka kwa wanafunzi na kampuni ili kuhakikisha mazingira salama na ya kitaalamu.",
    # --- student dashboard ---
    "Student Dashboard": "Dashibodi ya Mwanafunzi",
    "Browse Slots": "Vinjari Nafasi",
    "Complete your profile to start applying": "Kamilisha wasifu wako iliuanze kuomba",
    "Upload your Student ID and Results Matrix - the platform admin will verify your account.": "Pakia Kadi yako ya Utambulisho na Matokeo - msimamizi wa jukwaa atathibitisha akaunti yako.",
    "Complete Profile": "Kamilisha Wasifu",
    "University": "Chuo Kikuu",
    "Course": "Kozi",
    "Year / GPA": "Mwaka / GPA",
    "Account": "Akaunti",
    "Active": "Hai",
    "Recent Applications": "Maombi ya Karibuni",
    "Slot": "Nafasi",
    "Company": "Kampuni",
    "Status": "Hali",
    "Payment": "Malipo",
    "Paid": "Imelipwa",
    "Unpaid": "Haijalipwa",
    "Pending": "Inasubiri",
    "Pay Now": "Lipa Sasa",
    "You have not applied to any slots yet.": "Bado hujawahi kuomba nafasi yoyote.",
    # --- student applications ---
    "Track your placement requests and complete required payments.": "Fuatilia maombi yako ya uwekaji na ukamilishe malipo yanayohitajika.",
    "Timeline Complete": "Mtiririko Umekamilika",
    "Under Review": "Chini ya Uhakiki",
    "Payment Expired": "Muda wa Malipo Umeisha",
    "Awaiting Payment": "Inasubiri Malipo",
    "Payment verified, Placement confirmed": "Malipo yamethibitishwa, uwekaji umethibitishwa",
    "Pending company decision": "Inasubiri uamuzi wa kampuni",
    "Slot released - re-book to start a new application": "Nafasi imetolewa - weka tena iliuanze maombi mapya",
    "Pay by {{ deadline }} to keep your seat": "Lipa ifikapo {{ deadline }} iliuhifadhi nafasi yako",
    "Pay fee to proceed to review": "Lipa ada iliendelee na uhakiki",
    "Application letter attached": "Barua ya maombi imeambatanishwa",
    "Re-book Slot": "Weka Nafasi Tena",
    "No applications yet": "Hakuna maombi bado",
    "You have not applied to any slots.": "Hujawahi kuomba nafasi yoyote.",
    # --- marketplace ---
    "Find an IPT Slot": "Tafuta Nafasi ya IPT",
    "Browse and filter live training opportunities tailored to your academic background and career goals.": "Vinjari na chuja nafasi za mafunzo zinazopatikana kulingana na elimu yako na malengo yako ya kazi.",
    "Region": "Mkoa",
    "District": "Wilaya",
    "All Regions": "Mikoa Yote",
    "All Districts": "Wilaya Zote",
    "Department / Course": "Idara / Kozi",
    "Level / Year of Study": "Kiwango / Mwaka wa Masomo",
    "Education Level (TCU)": "Kiwango cha Elimu (TCU)",
    "Any Level": "Kiwango chochote",
    "Any Qualification": "Kiwango chochote",
    "Loading slots...": "Inapakia nafazi...",
    "Apply Now": "Omba Sasa",
    "Slot Full": "Nafasi Imejaa",
    "Verification required to apply.": "Uthibitisho unahitajika kuomba.",
    "/month": "/mwezi",
    "Dept:": "Idara:",
    "Available": "Zinapatikana",
    "FULL": "IMEJAA",
    "No slots match your filters.": "Hakuna nafasi zinazolingana na vichujio vyako.",
    "Loading...": "Inapakia...",
    "Year": "Mwaka",
    # --- student profile ---
    "Student Profile": "Wasifu wa Mwanafunzi",
    "Manage your academic details and how companies see you.": "Dhibiti taarifa zako za kielimu na jinsi kampuni zinavyokuona.",
    "Academic Information": "Taarifa za Kielimu",
    # --- documents ---
    "Document Vault": "Hifadhi ya Nyaraka",
    "Securely store and manage required documents. Ensure files are under 2MB and in PDF, PNG, JPG, DOC, or DOCX format.": "Hifadhi na dhibiti nyaraka muhimu kwa usalama. Hakikisha faili ni chini ya MB 2 na kwa umbizo la PDF, PNG, JPG, DOC, au DOCX.",
    "Upload Document": "Pakia Waraka",
    "Data Security": "Usalama wa Taarifa",
    "All uploaded documents are stored securely within the IPT network and reviewed by the admin.": "Nyaraka zote zilizopakiwa huhifadhiwa kwa usalama ndani ya mtandao wa IPT na hukaguliwa na msimamizi.",
    "Your Documents": "Nyaraka Zako",
    "Uploaded": "Imepakiwa",
    "No documents yet": "Hakuna nyaraka bado",
    "You haven't uploaded any documents. Use the form on the left to start building your vault.": "Hujapakia nyaraka yoyote. Tumia fomu upande wa kushoto kuanza kujenga hifadhi yako.",
    # --- apply ---
    "Apply": "Omba",
    "Apply to Slot": "Omba Nafasi",
    "Upload your university application letter to submit your application, then complete payment.": "Pakia barua yako ya maombi kutoka chuo kikuu iliutume ombi lako, kisha ukamilishe malipo.",
    "Back to slots": "Rudi kwenye nafazi",
    "Submit & Proceed to Payment": "Tuma & Endelea Kulipa",
    # --- company slots ---
    "My Slots": "Nafasi Zangu",
    "Manage your active training opportunities, track applications, and update availability across all your programs.": "Simamia nafasi zako za mafunzo, fuatilia maombi, na Sasisha upatikanaji wa programu zako zote.",
    "New Slot": "Nafasi Mpya",
    "Total Slots": "Nafasi Zote",
    "Total Spots": "Viti Vyote",
    "Spots Filled": "Viti Vilivyojazwa",
    "Total Applications": "Maombi Yote",
    "Stipend": "Posho",
    "/mo": "/mwezi",
    "Applicants": "Waombaji",
    "No slots created yet": "Hakuna nafazi zilizoundwa bado",
    "You haven't posted any training opportunities. Create your first slot to start receiving applications.": "Hujatangaza nafasi yoyote ya mafunzo. Unda nafasi yako ya kwanza ianzishe kupokea maombi.",
    "Create First Slot": "Unda Nafasi ya Kwanza",
    "Edit Slot": "Hariri Nafasi",
    "Post a New Slot": "Tangaza Nafasi Mpya",
    "Create a new opportunity for emerging talent.": "Unda fursa mpya kwa vipaji ibuka.",
    "Cancel": "Ghairi",
    "Save Changes": "Hifadhi Mabadiliko",
    "Post Slot": "Tangaza Nafasi",
    "Location Details": "Maelezo ya Mahali",
    "Separate skills with commas, e.g. Python, SQL, Communication.": "Tenganisha ujuzi kwa koma, mfano Python, SQL, Mawasiliano.",
    "Minimum TCU qualification: Level 4 Certificate to Level 10 PhD.": "Kiwango cha chini cha TCU: Cheti cha Kiwango 4 hadi Udaktari wa Kiwango 10.",
    "Offer a monthly stipend to interns.": "Toa posho ya mwezi kwa wanafunzi wanaofanya mazoezi.",
    # --- applicants ---
    "Filter by Education Level (TCU)": "Chuja kwa Kiwango cha Elimu (TCU)",
    "Any qualification": "Kiwango chochote",
    "Slot Overview": "Muhtasari wa Nafasi",
    "Accepted": "Imekubaliwa",
    "Pending Review": "Inasubiri Uhakiki",
    "Capacity": "Uwezo",
    "No applicants yet": "Hakuna waombaji bado",
    "Once students apply and complete their application fee payments, they will appear here for your review.": "Wanafunzi wakituma maombi na kukamilisha malipo yao ya ada, wataonekana hapa kwa uhakiki wako.",
    "View Application Letter": "Angalia Barua ya Maombi",
    "View Documents": "Angalia Nyaraka",
    "Acceptance Note (Optional)": "Ujumbe wa Kubali (Si lazima)",
    "Send Confirmation": "Tuma Uthibitisho",
    # --- python: forms ---
    "Skills (comma separated)": "Ujuzi (zikitenganishwa kwa koma)",
    "Separate skills with commas.": "Tenganisha ujuzi kwa koma.",
    "Profile photo": "Picha ya wasifu",
    "A clear headshot (max 2MB).": "Picha ya uso ulio wazi (chini ya MB 2).",
    "ID card photo": "Picha ya kadi ya utambulisho",
    "A clear photo of your student ID card (max 2MB).": "Picha ya wazi ya kadi yako ya utambulisho (chini ya MB 2).",
    "Required skills (comma separated)": "Ujuzi unaohitajika (zikitenganishwa kwa koma)",
    "University Application Letter": "Barua ya Maombi ya Chuo Kikuu",
    "The letter from your university requesting the placement (PDF/image/DOC, max 2MB).": "Barua kutoka chuo chikooomba uwekaji (PDF/picha/DOC, chini ya MB 2).",
    "Student ID Card": "Kadi ya Utambulisho ya Mwanafunzi",
    "Semester Results Matrix": "Matokeo ya Semista",
    "Curriculum Vitae": "Wasifu (CV)",
    "University Introduction Letter": "Barua ya Utambulisho kutoka Chuo",
    "BRELA Registration Certificate": "Cheti cha Usajili wa BRELA",
    "TIN Certificate": "Cheti cha TIN",
    "Business License": "Leseni ya Biashara",
    # --- python: choices ---
    "Approved": "Imeidhinishwa",
    "Rejected": "Imekataliwa",
    "Male": "Mwanaume",
    "Female": "Mwanamke",
    "Open": "Wazi",
    "Full": "Imejaa",
    "Paused": "Imesimamishwa",
    "Closed": "Imefungwa",
    # --- education levels (TCU) ---
    "Level 4 — Certificate": "Kiwango cha 4 — Cheti",
    "Level 5 — Technician Certificate": "Kiwango cha 5 — Cheti cha Mfundi",
    "Level 6 — Ordinary Diploma": "Kiwango cha 6 — Diploma",
    "Level 7 — Higher Diploma": "Kiwango cha 7 — Diploma ya Juu",
    "Level 8 — Bachelor's Degree": "Kiwango cha 8 — Shahada",
    "Level 9 — Master's Degree": "Kiwango cha 9 — Uzamili",
    "Level 10 — Doctorate (PhD)": "Kiwango cha 10 — Udaktari (PhD)",
    "Certificate": "Cheti",
    "Technician Certificate": "Cheti cha Mfundi",
    "Ordinary Diploma": "Diploma",
    "Higher Diploma": "Diploma ya Juu",
    "Bachelor's": "Shahada",
    "Master's": "Uzamili",
    "PhD": "Udaktari",
}

PO_HEADER = (
    'msgid ""\n'
    'msgstr ""\n'
    '"Project-Id-Version: ipt-marketplace\\n"\n'
    '"Report-Msgid-Bugs-To: \\n"\n'
    '"PO-Revision-Date: 2026-08-22 00:00+0300\\n"\n'
    '"Language: sw\\n"\n'
    '"MIME-Version: 1.0\\n"\n'
    '"Content-Type: text/plain; charset=UTF-8\\n"\n'
    '"Content-Transfer-Encoding: 8bit\\n"\n'
    '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"'
)


def po_escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def write_po(catalog):
    lines = [PO_HEADER, ""]
    for msgid in sorted(catalog):
        lines.append(f'msgid "{po_escape(msgid)}"')
        lines.append(f'msgstr "{po_escape(catalog[msgid])}"')
        lines.append("")
    LOCALE_DIR.joinpath("django.po").write_text("\n".join(lines), encoding="utf-8")


def write_mo(catalog):
    """Compile a GNU MO file (little endian) from the catalog dict."""
    keys = sorted(catalog)
    header_msgid = ""
    header_msgstr = PO_HEADER.split('msgstr ""', 1)[1].strip().replace('\n"', '\n"').replace('"', "")
    # Build proper header msgstr from PO_HEADER lines.
    header_lines = [
        "Project-Id-Version: ipt-marketplace\n",
        "Report-Msgid-Bugs-To: \n",
        "PO-Revision-Date: 2026-08-22 00:00+0300\n",
        "Language: sw\n",
        "MIME-Version: 1.0\n",
        "Content-Type: text/plain; charset=UTF-8\n",
        "Content-Transfer-Encoding: 8bit\n",
        "Plural-Forms: nplurals=2; plural=(n != 1);\n",
    ]
    entries = [(header_msgid.encode("utf-8"), "".join(header_lines).encode("utf-8"))]
    entries += [(k.encode("utf-8"), catalog[k].encode("utf-8")) for k in keys]

    n = len(entries)
    keystart = 7 * 4 + 16 * n
    valuestart = keystart + sum(len(k) + 1 for k, _ in entries)

    koffsets, voffsets = [], []
    kdata, vdata = b"", b""
    koffset, voffset = keystart, valuestart
    for k, v in entries:
        koffsets.append((len(k), koffset))
        kdata += k + b"\x00"
        koffset += len(k) + 1
        voffsets.append((len(v), voffset))
        vdata += v + b"\x00"
        voffset += len(v) + 1

    output = struct.pack(
        "<Iiiiiii",
        0x950412DE,      # magic
        0,               # version
        n,               # number of entries
        7 * 4,           # offset of key table
        7 * 4 + n * 8,   # offset of value table
        0,               # size of hash table
        7 * 4 + n * 8,   # offset of hash table
    )
    for length, off in koffsets:
        output += struct.pack("<ii", length, off)
    for length, off in voffsets:
        output += struct.pack("<ii", length, off)
    output += kdata + vdata
    LOCALE_DIR.joinpath("django.mo").write_bytes(output)


def main():
    LOCALE_DIR.mkdir(parents=True, exist_ok=True)
    write_po(CATALOG)
    write_mo(CATALOG)
    print(f"Wrote {LOCALE_DIR / 'django.po'}")
    print(f"Wrote {LOCALE_DIR / 'django.mo'} ({len(CATALOG)} strings)")


if __name__ == "__main__":
    main()

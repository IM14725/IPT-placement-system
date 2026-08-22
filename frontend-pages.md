# IPT Marketplace — Frontend Pages Guide

This document describes **only the user-facing pages** — what each screen looks like,
what elements appear, and how a visitor interacts with them. It contains no backend
details (no models, no APIs, no servers).

All pages are server-rendered HTML styled with **Tailwind CSS** (loaded from a CDN).
The visual language is consistent across the whole app:

- **Brand color**: indigo (`#4f46e5`) used for primary buttons and links.
- **Cards**: white, rounded corners, soft shadow, on a light-gray page background.
- **Type**: bold section headings, small gray secondary text.

---

## 1. Shared Layout (every page)

Every page except the bare home page shares the same skeleton:

- **Top navigation bar** (indigo background, white text):
  - Left: **"IPT Marketplace"** logo linking to the home page.
  - Right, **signed out**: `Login` (text link) and `Register` (white pill button).
  - Right, **signed in as a Student**: `Dashboard`, `Find a Slot`, `My Applications`,
    the user's email, and `Logout`.
  - Right, **signed in as a Company**: `Dashboard`, `Slots`, the user's email, and `Logout`.
  - Right, **signed in as an Admin**: `Ledger`, `Metrics`, `Admin`, the user's email, and `Logout`.
- **Message banners**: when the server shows a message (success / error / info),
  a colored strip appears right below the nav — green for success, red for error,
  blue for info.
- **Toast notifications** (bottom-right corner): small stacked pop-ups that appear
  briefly (6 seconds) and disappear automatically. They are used for live events
  (e.g., "Application Submitted") and in-page actions (e.g., payment success).
- **Live notification feed**: while signed in, the page keeps a WebSocket open. When a
  notification arrives, a success toast pops up automatically — no page reload needed.

---

## 2. Home Page (`/`)

A centered marketing landing page:

- Large headline: *"Connect students with companies for Industrial Practical Training."*
- Subtext explaining the marketplace in one sentence.
- Two buttons: **Get Started** (indigo, solid) and **Login** (outlined).
- Three feature cards side by side:
  1. **For Students** — verify your ID and results, browse slots by region/district/
     course/level, apply with one-tap mobile money.
  2. **For Companies** — post verified slots, review paid applicants, notify students instantly.
  3. **Secure & Verified** — strict document verification on both sides.

---

## 3. Create Account (`/auth/register/`)

A centered card (max width ~28rem) on a light-gray background:

- Heading: **"Create your account"**.
- A vertical form with labeled fields (email, name, phone, role choice, password).
  Each field shows inline red helper text if there is an error, and a small gray
  hint where available.
- A full-width indigo **"Create Account"** button.
- Below the form: *"Already registered? Login"* link.

---

## 4. Login (`/auth/login/`)

Same layout as registration:

- Heading: **"Login"**.
- Email + password fields, inline error text on failure.
- Full-width indigo **"Login"** button.
- Below: *"New here? Create an account"* link.

---

## 5. Student Dashboard (`/student/dashboard/`)

Personal home for a signed-in student:

- Top row: heading **"Student Dashboard"** with a **"Browse Slots"** button (indigo)
  on the right.
- **If the profile is incomplete**: a yellow alert card with text telling the student
  to complete their profile, plus a **"Complete Profile"** button.
- **If the profile exists**, a row of four stat cards:
  1. **University** (name, truncated if long)
  2. **Course**
  3. **Year / GPA** (e.g., "Year 3 · 3.85")
  4. **Verification** (colored pill: green **Approved**, red **Rejected**, or
     yellow **Pending**)
- **Recent Applications** section: a table with columns `#`, `Slot`, `Company`,
  `Status`, `Payment`. Rows show the application number, slot title, company name,
  a colored status word (**Accepted** green / **Paid** blue / **Pending Payment** yellow),
  and either a "Pay Now" link (unpaid) or the paid amount (green).
- Empty state: *"You have not applied to any slots yet."*

---

## 6. Student Profile (`/student/profile/`)

Two-column layout:

- **Left card — profile form**: fields for university, course, year of study, GPA,
  region, district, and skills (comma-separated). The **region** and **district**
  dropdowns are cascaded: choosing a region automatically loads its districts into
  the district dropdown (spinner-free, instant). A **"Save Profile"** indigo button
  at the bottom.
- **Right card — verification status**: shows the current status as a label,
  plus the admin's rejection reason in red if the profile was rejected.

---

## 7. Document Vault (`/student/documents/`)

Two-column layout:

- **Left card — upload form**: heading **"Document Vault"**, a note that files must
  be under **2 MB** and of allowed types (PDF, PNG, JPG, DOC, DOCX). A dropdown to
  pick the document kind (Student ID Card, Results Matrix, CV, Introduction Letter),
  a file picker, and an **"Upload"** button. Errors (e.g., oversize file) appear as
  red text under the field.
- **Right card — "Your Documents"**: a list of uploaded files, each showing the
  document type, file name, size (human-readable, e.g., "1.2 MB"), and upload date,
  with a **"View"** link that opens the file in a new tab.
- Empty state: *"No documents uploaded yet."*

---

## 8. Marketplace / Find a Slot (`/student/marketplace/`)

The slot-browsing page:

- Heading **"Find an IPT Slot"** with a one-line subtitle about live filtering.
- **If not verified**: a yellow banner explaining the account status (with a link to
  upload documents), or a link to complete the profile if none exists.
- **Filter bar** (white card with four controls side by side):
  1. **Region** dropdown ("All regions" by default).
  2. **District** dropdown — populated after a region is chosen ("All districts" by default).
  3. **Department / Course** text input (free text, e.g., "Computer Science").
  4. **Level / Year of Study** dropdown (Any level, Year 1–6).
- **Results grid**: two-column cards. Each slot card shows:
  - Slot **title** and **company name** (indigo text).
  - A status pill on the right: green **"N available"** or red **"FULL"**.
  - Meta line: industry · role type.
  - Location line: region · district · street (street only if present).
  - Department and/or level lines (only if set).
  - Stipend: green "Stipend: TZS 100,000" or gray "Unpaid".
  - Required skills rendered as small gray rounded tags.
  - Bottom action:
    - **"Apply Now"** button (full-width indigo) when the student is verified and
      the slot has space.
    - A yellow note "Verification required to apply." when not verified.
    - A red **"Slot Full"** pill when there is no space.
- Results update **live** as filters change (department filters debounce typing).

---

## 9. My Applications (`/student/applications/`)

A vertical list of application cards, newest first. Each card:

- Top row: `#<id> — <slot title>` (bold) and company · district (gray).
- Status pill on the right: green **Accepted**, blue **Paid & Verified**, or yellow
  **Pending Payment**.
- **Progress timeline** (for each application): two connected steps —
  1. **Paid** (green circle with a checkmark when done)
  2. **Accepted** (green circle with a checkmark when done)

  Completed steps show a green circle with a white ✓ and green labels connected by a
  green line; pending steps are gray.
- Payment action on the right:
  - Unpaid: an indigo **"Pay TZS 15,000"** button linking to the payment page.
  - Paid: green text showing the reference, e.g., *"Paid · IPT-6C059526FB"*.
- Empty state: *"You have not applied to any slots yet"* with a "Browse slots" link.

---

## 10. Pay Application Fee (`/student/payments/<id>/`)

A single centered card:

- Heading **"Pay Application Fee"**.
- Context lines: Slot, Company, and the payment **reference** (monospace).
- A prominent fee box (gray, centered): uppercase "AMOUNT DUE" over a large
  bold "TZS 15,000".
- **Payment Method** dropdown: M-Pesa, Tigo Pesa, Airtel Money.
- **Mobile Number** text field (pre-filled with the student's registered number).
- A full-width indigo **"Pay Now"** button.
- A status line under the button that reads "Contacting gateway..." then
  "Confirming payment..." while processing (button disables and reads "Processing...").
- On success: a green toast ("Payment successful") and the page redirects to
  **My Applications** after a short delay. On failure: a red toast and the button
  re-enables.

---

## 11. Company Dashboard (`/company/dashboard/`)

Personal home for a signed-in company:

- Heading **"Company Dashboard"**.
- **Onboarding banners** depending on state:
  - No profile yet: yellow card — "Welcome! Complete your company profile..." with a
    **"Go to profile"** link.
  - Pending / rejected: yellow card showing the status label (e.g., "Pending") and,
    if rejected, the admin's reason.
  - Approved: green card — "Approved. You can now post slots, review applicants and
    send acceptance SMS."
- **Three stat cards**: **Company** (name + industry), **Verification** (status label),
  and **Active Slots** (count).
- **Recent Slots** section: heading with a **"New Slot"** button (indigo). Each row
  shows the slot title, a gray line (district · status · spots), and a
  **"View applicants"** link.
- Empty state: *"No slots yet"* with a "Post your first slot" link.

---

## 12. Company Profile (`/company/profile/`)

Two-column layout:

- **Left card — profile form**: fields for company name, industry, description,
  street, region, and district. Region→district dropdowns are cascaded (same behavior
  as the student profile). A **"Save Profile"** button.
- **Right column** (stacked cards):
  1. **Corporate Documents** — instructions ("Upload BRELA certificate, TIN and
     business license (< 2MB each)"), a dropdown for the document kind, a file
     picker, and an **"Upload"** button. Below, the list of already-uploaded docs
     (kind + file name).
  2. **Verification Status** — current status label, plus the rejection reason in
     red when rejected.

---

## 13. My Slots (`/company/slots/`)

A management list for company-posted slots:

- Heading **"My Slots"** with a **"New Slot"** button (indigo) on the right.
- A white card list; each slot row shows:
  - Bold **title**.
  - Gray meta line: industry · role type · district (plus department and year if set).
  - A status pill: green **Open**, red **Full**, or gray **Paused**.
  - "N/M spots left" (e.g., "3/5 spots left").
  - Green "TZS 100,000" if a stipend is offered.
- Action links on the right: **Applicants**, **Edit**, **Pause/Resume** (toggle),
  and **Delete** (red, asks for confirmation).
- Empty state: *"You have no slots"* with a "Post your first slot" link.

---

## 14. New / Edit Slot (`/company/slots/new/` and `/company/slots/<id>/edit/`)

A single centered card (max width ~42rem):

- Heading **"Post a New Slot"** or **"Edit Slot"**.
- A vertical form with labeled fields: title, description, industry, role type,
  district (with a cascading region selector above it), street, department,
  level/year, capacity, a stipend checkbox, stipend amount, and required skills
  (comma-separated). Required fields are marked with an asterisk.
- Bottom: an indigo **"Post Slot"** / **"Save Changes"** button.
- The heading for the edit form indicates the slot being edited.

---

## 15. Applicants (`/company/slots/<id>/applicants/`)

A review list for one slot:

- Heading **"Applicants"** with a gray subtitle: slot title · company · spots left
  (e.g., "1/2 spots left").
- A white card list; each applicant row shows:
  - Bold **full name**.
  - Gray meta: university · course · year · district.
  - A green pill with the application status (e.g., **Accepted**).
  - If paid: gray "Paid TZS 15,000 · <reference>".
- Actions on the right:
  - **"View documents"** link (always for paid applicants).
  - **"Accept & SMS"** inline form (optional note text box + green button) for any
    paid application. Submitting it sends the acceptance SMS.
- Empty state: *"No paid applicants yet."*

---

## 16. Applicant Documents (`/company/applicants/<id>/documents/`)

- Heading **"Applicant Documents"** with a gray subtitle (applicant name · university
  · slot).
- A white card list; each document row shows the document type, file name, upload
  date, and a **"View"** link (opens in a new tab).
- Empty state: *"This applicant has not uploaded any documents."*

---

## 17. Payment Ledger (`/platform/ledger/`) — Admin only

- Heading **"Payment Ledger"**.
- Two stat cards: **Total Fees Collected** (large bold TZS amount) and
  **Paid Transactions** (count).
- A wide table with columns: `Reference` (monospace), `Student`, `Slot`, `Company`,
  `Amount`, `Paid At` (date + time).
- Empty state: *"No verified payments yet."*

---

## 18. Platform Metrics (`/platform/metrics/`) — Admin only

- Heading **"Platform Metrics"**.
- A responsive grid of stat cards:
  - **Users**
  - **Students** (with "N verified" in green)
  - **Companies** (with "N approved" in green)
  - **Slots** (with "N open · N full")
  - **Applications** (with "N paid · N accepted")
  - **Payments**
  - **Total Fees Collected** (spans two columns)

---

## 19. Access Denied (`403`)

A simple centered page:

- Large **403** numeral.
- Heading **"Access Denied"** and a short gray line: *"You do not have permission to
  view this page."*

---

## Page Map

| Page | URL | Role |
|---|---|---|
| Home | `/` | Public |
| Create Account | `/auth/register/` | Public |
| Login | `/auth/login/` | Public |
| Student Dashboard | `/student/dashboard/` | Student |
| Student Profile | `/student/profile/` | Student |
| Document Vault | `/student/documents/` | Student |
| Marketplace | `/student/marketplace/` | Student |
| My Applications | `/student/applications/` | Student |
| Pay Application Fee | `/student/payments/<id>/` | Student |
| Company Dashboard | `/company/dashboard/` | Company |
| Company Profile | `/company/profile/` | Company |
| My Slots | `/company/slots/` | Company |
| New / Edit Slot | `/company/slots/new/` · `/company/slots/<id>/edit/` | Company |
| Applicants | `/company/slots/<id>/applicants/` | Company |
| Applicant Documents | `/company/applicants/<id>/documents/` | Company |
| Payment Ledger | `/platform/ledger/` | Admin |
| Platform Metrics | `/platform/metrics/` | Admin |
| Access Denied | — (403) | Any |
import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User, UserRole
from apps.companies.models import Organization
from apps.core.models import AuditLog, Report
from apps.students.models import StudentProfile, University, VerificationStatus

PW = "Secret123!x"


@pytest.fixture
def admin_client(db):
    admin = User.objects.create_user(email="admin@x.tz", password=PW, role=UserRole.SUPER_ADMIN, is_staff=True)
    client = Client()
    client.force_login(admin)
    return client


@pytest.fixture
def student(db):
    user = User.objects.create_user(email="s@x.tz", password=PW, role=UserRole.STUDENT)
    return StudentProfile.objects.create(
        user=user, course="CS", academic_year=3, verification_status=VerificationStatus.APPROVED
    )


@pytest.fixture
def organization(db):
    owner = User.objects.create_user(email="o@x.tz", password=PW, role=UserRole.ORGANIZATION)
    return Organization.objects.create(
        user=owner, name="Acme Ltd", industry="Tech", verification_status=VerificationStatus.APPROVED
    )


def _post(client, url_name, **data):
    return client.post(reverse(url_name), data)


# ── universities ─────────────────────────────────────────────────────────────


def test_non_admin_cannot_open_universities(student):
    client = Client()
    client.force_login(student.user)
    assert client.get(reverse("platform-universities")).status_code == 403
    assert _post(client, "platform-universities", action="create", name="X Univ").status_code == 403
    assert not University.objects.filter(name="X Univ").exists()


def test_admin_adds_university(admin_client):
    resp = _post(admin_client, "platform-universities", action="create", name="New Univ", institution_type="UNIVERSITY")
    assert resp.status_code == 302
    uni = University.objects.get(name="New Univ")
    assert uni.is_approved
    assert AuditLog.objects.filter(action="University Added").exists()


def test_admin_cannot_add_duplicate_university(admin_client):
    University.objects.create(name="Dup Univ")
    resp = _post(admin_client, "platform-universities", action="create", name="dup univ", institution_type="UNIVERSITY")
    assert resp.status_code == 200
    assert University.objects.filter(name__iexact="dup univ").count() == 1


def test_admin_approves_suggestion(admin_client, student):
    uni = University.objects.create(
        name="Pending Univ", verification_status=VerificationStatus.PENDING, suggested_by=student.user
    )
    _post(admin_client, "platform-universities", action="approve", id=str(uni.id))
    uni.refresh_from_db()
    assert uni.is_approved
    assert AuditLog.objects.filter(action="University Approved").exists()


def test_admin_rejects_suggestion_and_unlinks_students(admin_client, student):
    uni = University.objects.create(name="Bad Univ", verification_status=VerificationStatus.PENDING)
    student.university = uni
    student.save()
    _post(admin_client, "platform-universities", action="reject", id=str(uni.id))
    uni.refresh_from_db()
    student.refresh_from_db()
    assert uni.verification_status == VerificationStatus.REJECTED
    assert student.university is None


def test_admin_merges_suggestion_into_existing(admin_client, student):
    target = University.objects.create(name="Real Univ", abbreviation="RU")
    dup = University.objects.create(name="Real Univerisity", verification_status=VerificationStatus.PENDING)
    student.university = dup
    student.save()
    _post(admin_client, "platform-universities", action="merge", id=str(dup.id), target=str(target.id))
    student.refresh_from_db()
    assert student.university == target
    assert not University.objects.filter(pk=dup.pk).exists()


# ── suspend / reactivate ─────────────────────────────────────────────────────


def test_suspend_requires_reason(admin_client, student):
    _post(admin_client, "platform-account-status", kind="student", id=str(student.id), action="suspend", reason="")
    student.user.refresh_from_db()
    assert student.user.is_active and student.user.suspended_at is None


def test_suspend_and_reactivate_student(admin_client, student):
    _post(admin_client, "platform-account-status", kind="student", id=str(student.id), action="suspend", reason="Fake documents")
    student.user.refresh_from_db()
    assert not student.user.is_active
    assert student.user.suspended_at is not None
    assert student.user.suspension_reason == "Fake documents"
    assert AuditLog.objects.filter(action="Account Suspended").exists()

    assert not Client().login(email="s@x.tz", password=PW)

    _post(admin_client, "platform-account-status", kind="student", id=str(student.id), action="reactivate")
    student.user.refresh_from_db()
    assert student.user.is_active and student.user.suspended_at is None
    assert AuditLog.objects.filter(action="Account Reactivated").exists()


def test_suspend_organization_covers_members_and_hides_it(admin_client, organization):
    _post(admin_client, "platform-account-status", kind="organization", id=str(organization.id), action="suspend", reason="Scam reports")
    organization.refresh_from_db()
    organization.user.refresh_from_db()
    assert organization.is_suspended
    assert not organization.user.is_active

    _post(admin_client, "platform-account-status", kind="organization", id=str(organization.id), action="reactivate")
    organization.refresh_from_db()
    organization.user.refresh_from_db()
    assert not organization.is_suspended
    assert organization.user.is_active


def test_reactivate_keeps_deletion_requested_accounts_inactive(admin_client, student):
    from django.utils import timezone

    student.user.deletion_requested_at = timezone.now()
    student.user.save()
    _post(admin_client, "platform-account-status", kind="student", id=str(student.id), action="suspend", reason="x")
    _post(admin_client, "platform-account-status", kind="student", id=str(student.id), action="reactivate")
    student.user.refresh_from_db()
    assert not student.user.is_active


def test_non_admin_cannot_suspend(student, organization):
    client = Client()
    client.force_login(student.user)
    resp = _post(client, "platform-account-status", kind="organization", id=str(organization.id), action="suspend", reason="x")
    assert resp.status_code == 403
    organization.refresh_from_db()
    assert not organization.is_suspended


def test_suspend_ignores_offsite_next_url(admin_client, student):
    resp = admin_client.post(
        reverse("platform-account-status"),
        {"kind": "student", "id": str(student.id), "action": "suspend", "reason": "x", "next": "https://evil.example/"},
    )
    assert resp.status_code == 302
    assert "evil.example" not in resp["Location"]


def test_directory_lists_suspended_accounts(admin_client, student):
    _post(admin_client, "platform-account-status", kind="student", id=str(student.id), action="suspend", reason="x")
    url = reverse("platform-directory")
    admin_client.get(url)  # shows (and clears) the "Suspended student ..." flash message
    active = admin_client.get(url).content
    at = active.find(b"s@x.tz")
    assert at == -1, active[max(0, at - 200) : at + 60]
    assert b"s@x.tz" in admin_client.get(url, {"account": "suspended"}).content


# ── reports ──────────────────────────────────────────────────────────────────


def test_user_files_report(student, organization):
    client = Client()
    client.force_login(student.user)
    resp = client.post(
        reverse("report-submit"),
        {"category": "FRAUD", "organization": str(organization.id), "subject": "Asked me for money", "description": "Details"},
    )
    assert resp.status_code == 302
    report = Report.objects.get()
    assert report.reporter == student.user
    assert report.organization == organization
    assert report.status == Report.Status.OPEN


def test_reporter_sees_own_reports_with_status_but_not_admin_notes(student, organization):
    mine = Report.objects.create(
        reporter=student.user, subject="Asked me for money", description="d",
        status=Report.Status.RESOLVED, admin_notes="INTERNAL-ONLY",
    )
    Report.objects.create(reporter=organization.user, subject="Someone else's report", description="d")
    client = Client()
    client.force_login(student.user)
    html = client.get(reverse("report-submit")).content.decode()
    assert mine.subject in html and "Resolved" in html
    assert "INTERNAL-ONLY" not in html
    assert "Someone else&#x27;s report" not in html and "Someone else's report" not in html


def test_students_and_organizations_have_a_report_link_in_the_menu(student, organization):
    for user, url in ((student.user, "student-dashboard"), (organization.user, "organization-dashboard")):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse(url), follow=True)
        assert f'href="{reverse("report-submit")}"' in resp.content.decode()


def test_anonymous_cannot_file_report(db):
    resp = Client().post(reverse("report-submit"), {"category": "FRAUD", "subject": "x", "description": "y"})
    assert resp.status_code == 302
    assert "login" in resp["Location"]
    assert not Report.objects.exists()


def test_admin_updates_report(admin_client, student):
    report = Report.objects.create(reporter=student.user, subject="Bad slot", description="d")
    resp = _post(admin_client, "platform-reports", id=str(report.id), status="RESOLVED", admin_notes="Removed slot")
    assert resp.status_code == 302
    report.refresh_from_db()
    assert report.status == Report.Status.RESOLVED
    assert report.admin_notes == "Removed slot"
    assert report.handled_by is not None
    assert AuditLog.objects.filter(action="Report Updated").exists()


def test_non_admin_cannot_view_reports(student):
    client = Client()
    client.force_login(student.user)
    assert client.get(reverse("platform-reports")).status_code == 403

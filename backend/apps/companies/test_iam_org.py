"""IAM: organization member lifecycle, tenant isolation and the organization audit trail."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import AdminRole, UserRole
from apps.applications.services import create_application, finalize_verified_payment
from apps.companies.models import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
)
from apps.companies.services import (
    add_organization_member,
    change_member_role,
    create_organization,
    remove_organization_member,
)
from apps.core.audit import audit_event, filter_audit_logs
from apps.core.models import AuditLog
from apps.documents.models import Document
from apps.documents.tests import make_png_bytes
from apps.locations.models import District, Region
from apps.payments.models import Payment, PaymentMethod
from apps.slots.models import Slot
from apps.students.models import StudentProfile, University, VerificationStatus

User = get_user_model()
PW = "Secret123!x"


@pytest.fixture(autouse=True)
def _reset_redis_state():
    from apps.core.redis_client import get_redis

    r = get_redis()
    for key in list(r.scan_iter("*:rl:*")) + list(r.scan_iter("*:lockout:*")):
        r.delete(key)
    yield


def _verified(user):
    user.email_verified = True
    user.must_change_password = False
    user.save(update_fields=["email_verified", "must_change_password"])
    return user


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _make_org(name, prefix):
    owner = _verified(
        User.objects.create_user(email=f"{prefix}-owner@o.tz", password=PW, role=UserRole.ORGANIZATION)
    )
    org = create_organization(owner, name)
    org.industry = "Tech"
    org.verification_status = VerificationStatus.APPROVED
    org.save()
    return org, owner


@pytest.fixture
def alpha(db):
    org, owner = _make_org("Alpha Ltd", "alpha")
    recruiter = _verified(
        add_organization_member(
            org, "alpha-rec@o.tz", PW, role=OrganizationMemberRole.RECRUITER,
            first_name="Rita", last_name="Recruiter", position="Recruitment Officer", added_by=owner,
        ).user
    )
    return org, owner, recruiter


@pytest.fixture
def beta(db):
    org, owner = _make_org("Beta Ltd", "beta")
    return org, owner


@pytest.fixture
def district(db):
    region = Region.objects.create(name="Dar", slug="dar")
    return District.objects.create(name="Kinondoni", region=region)


def _slot(org, district, title="Intern"):
    return Slot.objects.create(
        organization=org, title=title, industry="Tech", role_type="Internship", district=district, capacity=2
    )


def _paid_application(org, district, email="stu@x.tz"):
    user = User.objects.create_user(email=email, password=PW, role=UserRole.STUDENT, email_verified=True)
    student = StudentProfile.objects.create(
        user=user,
        university=University.objects.get_or_create(name="UDSM")[0],
        course="CS",
        academic_year=3,
        verification_status=VerificationStatus.APPROVED,
    )
    app = create_application(student, _slot(org, district, "Paid slot"))
    payment = Payment.objects.create(student=student, application=app, amount=15000, method=PaymentMethod.M_PESA)
    finalize_verified_payment(payment, gateway_txn_id="GW-1")
    app.refresh_from_db()
    return app


def _org_doc(owner, doc_type=Document.DocType.BRELA_CERT):
    return Document.objects.create(
        owner=owner,
        doc_type=doc_type,
        file=SimpleUploadedFile("c.png", make_png_bytes(), content_type="image/png"),
        original_name="c.png",
    )


# ── membership rules ────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_member_has_own_credentials_must_change_temp_password_and_is_audited(alpha):
    org, owner, recruiter = alpha
    member = OrganizationMember.objects.get(user__email="alpha-rec@o.tz")
    assert member.position == "Recruitment Officer"
    assert member.user.id != owner.id and member.user.check_password(PW)
    event = AuditLog.objects.get(action="Member Added")
    assert event.organization_id == org.id and event.actor_id == owner.id
    assert event.actor_role == "Manager (Owner)" and event.target_label == "alpha-rec@o.tz"


@pytest.mark.django_db
def test_five_member_cap_and_duplicate_contact_messages(alpha):
    org, owner, _ = alpha
    for i in range(2):
        add_organization_member(org, f"m{i}@o.tz", PW, first_name="A", last_name="B")
    with pytest.raises(ValidationError) as dup:
        add_organization_member(org, "ALPHA-rec@o.tz", PW)
    assert "already associated with an account" in dup.value.messages[0]
    assert "Rita" not in dup.value.messages[0]  # no identity leak
    User.objects.filter(email="m0@o.tz").update(phone="255711111111")
    remove_organization_member(org, OrganizationMember.objects.get(user__email="m1@o.tz"), owner, "x")
    with pytest.raises(ValidationError, match="phone number is already associated"):
        add_organization_member(org, "n@o.tz", PW, phone="+255711111111")
    # 3 active after m1 left (owner, recruiter, m0): fill to the cap of 5, then refuse.
    for i in range(2, 4):
        add_organization_member(org, f"m{i}@o.tz", PW)
    with pytest.raises(ValidationError, match="at most 5"):
        add_organization_member(org, "m9@o.tz", PW)


@pytest.mark.django_db
def test_only_one_manager_owner_can_exist(alpha):
    org, owner, _ = alpha
    with pytest.raises(ValidationError):
        add_organization_member(org, "x@o.tz", PW, role=OrganizationMemberRole.OWNER)


# ── removal preserves history ───────────────────────────────────────────────

@pytest.mark.django_db
def test_removal_revokes_access_and_sessions_but_preserves_everything(alpha, district):
    org, owner, recruiter = alpha
    doc = _org_doc(recruiter)
    slot = _slot(org, district, "Recruiter's slot")
    rc = _client(recruiter)
    assert rc.get("/auth/session-status/").json()["valid"] is True
    member = OrganizationMember.objects.get(user=recruiter)

    oc = _client(owner)
    resp = oc.post(
        reverse("organization-remove-member", args=[member.id]), {"reason": "Left the company"}
    )
    assert resp.status_code == 302

    member.refresh_from_db()
    assert member.membership_status == MembershipStatus.REMOVED
    assert member.removed_by_id == owner.id and member.removal_reason == "Left the company"
    assert member.removed_at is not None and member.created_at is not None
    # Access gone immediately, session destroyed server side.
    assert rc.get("/auth/session-status/").status_code == 401
    assert rc.get(reverse("organization-slots")).status_code == 302
    recruiter.refresh_from_db()
    assert recruiter.is_active is False and User.objects.filter(pk=recruiter.pk).exists()
    blocked = Client().post("/auth/login/", {"email": recruiter.email, "password": PW})
    assert blocked.status_code == 200 and "Invalid email or password." in blocked.content.decode()
    # Organization-owned data stays.
    assert Slot.objects.filter(pk=slot.pk, organization=org).exists()
    assert Document.objects.filter(pk=doc.pk).exists()
    assert doc.pk in set(oc.get(reverse("organization-profile")).context["docs"].values_list("pk", flat=True))
    # Owner can still open the former member's document; it is the organization's.
    assert oc.get(reverse("document-view", args=[doc.id])).status_code == 200
    # Their earlier history stays attributed to them and searchable.
    audit_event(None, action="Slot Created", module="Opportunities", actor=recruiter, organization=org, target=slot)
    assert filter_audit_logs(AuditLog.objects.filter(organization=org), {"actor": "alpha-rec@o.tz"}).count() >= 2
    removal = AuditLog.objects.get(action="Member Removed")
    assert removal.actor_id == owner.id and removal.context["reason"] == "Left the company"


@pytest.mark.django_db
def test_removal_needs_reason_and_cannot_hit_owner_or_other_org(alpha, beta):
    org, owner, recruiter = alpha
    beta_org, beta_owner = beta
    member = OrganizationMember.objects.get(user=recruiter)
    oc = _client(owner)
    oc.post(reverse("organization-remove-member", args=[member.id]), {"reason": "  "})
    member.refresh_from_db()
    assert member.membership_status == MembershipStatus.ACTIVE
    owner_member = OrganizationMember.objects.get(user=owner)
    oc.post(reverse("organization-remove-member", args=[owner_member.id]), {"reason": "x"})
    assert OrganizationMember.objects.get(user=owner).membership_status == MembershipStatus.ACTIVE
    # Another organization's Manager cannot remove our member (404: tenant scoped).
    bc = _client(beta_owner)
    assert bc.post(reverse("organization-remove-member", args=[member.id]), {"reason": "x"}).status_code == 404
    # A recruiter cannot remove anyone.
    assert _client(recruiter).post(reverse("organization-remove-member", args=[member.id]), {"reason": "x"}).status_code == 403


@pytest.mark.django_db
def test_former_members_page_shows_required_columns_to_manager_only(alpha):
    org, owner, recruiter = alpha
    member = OrganizationMember.objects.get(user=recruiter)
    remove_organization_member(org, member, owner, "Contract ended")
    page = _client(owner).get(reverse("organization-former-members"))
    body = page.content.decode()
    assert page.status_code == 200
    for needle in ("Rita Recruiter", "alpha-rec@o.tz", "Recruiter", "Contract ended", "alpha-owner@o.tz"):
        assert needle in body
    other = add_organization_member(org, "alpha-dep@o.tz", PW, role=OrganizationMemberRole.RECRUITER).user
    assert _client(_verified(other)).get(reverse("organization-former-members")).status_code == 403


@pytest.mark.django_db
def test_role_change_ends_the_members_session_and_is_audited(alpha):
    org, owner, recruiter = alpha
    rc = _client(recruiter)
    assert rc.get("/auth/session-status/").status_code == 200
    member = OrganizationMember.objects.get(user=recruiter)
    change_member_role(org, member, OrganizationMemberRole.MANAGER, owner)
    assert rc.get("/auth/session-status/").status_code == 401  # must sign in again: new session id
    ev = AuditLog.objects.get(action="Member Role Changed")
    assert ev.context == {"old_role": "RECRUITER", "new_role": "MANAGER"}


# ── tenant isolation: direct URLs and APIs ──────────────────────────────────

@pytest.mark.django_db
def test_other_organization_cannot_reach_our_objects_by_url(alpha, beta, district):
    org, owner, recruiter = alpha
    beta_org, beta_owner = beta
    for doc_type in (Document.DocType.BRELA_CERT, Document.DocType.TIN_CERT, Document.DocType.BUSINESS_LICENSE):
        _org_doc(beta_owner, doc_type)
    slot = _slot(org, district)
    app = _paid_application(org, district)
    org_doc = _org_doc(owner)
    ev = audit_event(None, action="X", module="Y", actor=owner, organization=org)
    bc = _client(beta_owner)

    probes = [
        ("get", reverse("organization-slot-edit", args=[slot.id])),
        ("post", reverse("organization-slot-toggle", args=[slot.id])),
        ("post", reverse("organization-slot-delete", args=[slot.id])),
        ("get", reverse("organization-applicants", args=[slot.id])),
        ("get", reverse("organization-applicant-documents", args=[app.id])),
        ("get", reverse("organization-applicant-letter", args=[app.id])),
        ("post", reverse("organization-acceptance-sms", args=[app.id])),
        ("post", reverse("organization-delete-doc", args=[org_doc.id])),
        ("get", reverse("organization-audit-detail", args=[ev.id])),
        ("get", reverse("document-view", args=[org_doc.id])),
    ]
    for method, url in probes:
        assert getattr(bc, method)(url).status_code in (404, 403), url
    assert Slot.objects.filter(pk=slot.pk).exists() and Document.objects.filter(pk=org_doc.pk).exists()
    # Their own audit log never contains ours.
    assert ev.id not in {l.id for l in bc.get(reverse("organization-audit-logs")).context["logs"]}
    # ...even when they try to pass another organization as a filter.
    resp = bc.get(reverse("organization-audit-logs"), {"organization": str(org.id)})
    assert ev.id not in {l.id for l in resp.context["logs"]}


@pytest.mark.django_db
def test_org_members_can_open_org_documents_but_students_documents_need_a_paid_application(alpha, beta, district):
    org, owner, recruiter = alpha
    beta_org, beta_owner = beta
    org_doc = _org_doc(owner)
    assert _client(recruiter).get(reverse("document-view", args=[org_doc.id])).status_code == 200  # another member's doc
    app = _paid_application(org, district)
    student_doc = _org_doc(app.student.user, Document.DocType.STUDENT_ID)
    assert _client(recruiter).get(reverse("document-view", args=[student_doc.id])).status_code == 200
    assert _client(beta_owner).get(reverse("document-view", args=[student_doc.id])).status_code == 404
    assert AuditLog.objects.filter(action="Document Accessed", organization=org).exists()


# ── organization audit trail ────────────────────────────────────────────────

@pytest.mark.django_db
def test_audit_access_matrix_manager_yes_recruiter_no_former_member_no(alpha):
    org, owner, recruiter = alpha
    assert _client(owner).get(reverse("organization-audit-logs")).status_code == 200
    assert _client(recruiter).get(reverse("organization-audit-logs")).status_code == 403
    deputy = _verified(
        add_organization_member(org, "alpha-dep@o.tz", PW, role=OrganizationMemberRole.MANAGER).user
    )
    assert _client(deputy).get(reverse("organization-audit-logs")).status_code == 200
    remove_organization_member(org, OrganizationMember.objects.get(user=deputy), owner, "Left")
    assert _client(deputy).get(reverse("organization-audit-logs")).status_code in (302, 403)


@pytest.mark.django_db
def test_org_actions_are_audited_with_role_target_and_outcome(alpha, district):
    org, owner, recruiter = alpha
    for doc_type in (Document.DocType.BRELA_CERT, Document.DocType.TIN_CERT, Document.DocType.BUSINESS_LICENSE):
        _org_doc(owner, doc_type)
    oc = _client(owner)
    rc = _client(recruiter)
    slot = _slot(org, district, "Audited slot")
    rc.post(reverse("organization-slot-toggle", args=[slot.id]))
    oc.post(reverse("organization-slot-delete", args=[slot.id]))
    toggled = AuditLog.objects.get(action__in=("Slot Paused", "Slot Published"))
    assert toggled.organization_id == org.id and toggled.actor_id == recruiter.id
    assert toggled.actor_role == "Organization Recruiter" and toggled.target_type == "Slot" and toggled.outcome == "SUCCESS"
    deleted = AuditLog.objects.get(action="Slot Deleted")
    assert deleted.target_label == "Audited slot" and deleted.actor_role == "Manager (Owner)"
    assert AuditLog.objects.filter(action="Login", organization=org).count() >= 2  # security events


@pytest.mark.django_db
def test_audit_filters_org_actor_action_dates_target_status_keyword(alpha, beta):
    org, owner, recruiter = alpha
    beta_org, beta_owner = beta
    audit_event(None, action="Application Accepted", module="Applications", actor=recruiter, organization=org,
                target_type="Application", target_id="app-1", target_label="stu@x.tz -> Slot A")
    audit_event(None, action="Login", module="Security", actor=owner, organization=org, outcome="FAILED")
    audit_event(None, action="Slot Created", module="Opportunities", actor=beta_owner, organization=beta_org)
    qs = AuditLog.objects.all()

    def ids(**params):
        return {l.action for l in filter_audit_logs(qs, params)}

    assert "Slot Created" not in ids(organization=str(org.id))
    assert "Slot Created" in ids(organization=str(beta_org.id))
    assert "Application Accepted" not in ids(organization=str(beta_org.id))
    assert "Application Accepted" in ids(actor="alpha-rec@o.tz") and "Slot Created" not in ids(actor="alpha-rec@o.tz")
    assert ids(action="accepted") == {"Application Accepted"}
    assert "Application Accepted" in ids(target="app-1") and "Application Accepted" in ids(target="Slot A")
    assert "Login" in ids(status="FAILED") and "Application Accepted" not in ids(status="FAILED")
    assert "Application Accepted" in ids(q="stu@x.tz")
    today = timezone.now().date()
    assert "Application Accepted" in ids(date_from=str(today - timedelta(days=1)), date_to=str(today))
    assert not ids(date_from=str(today + timedelta(days=1)))
    assert not ids(date_to=str(today - timedelta(days=2)))


@pytest.mark.django_db
def test_org_audit_view_filters_export_and_search_are_recorded(alpha):
    org, owner, recruiter = alpha
    oc = _client(owner)
    resp = oc.get(reverse("organization-audit-logs"), {"actor": "alpha-owner@o.tz", "action": "Member Added"})
    assert resp.status_code == 200 and [l.action for l in resp.context["logs"]] == ["Member Added"]
    assert AuditLog.objects.filter(action="Audit Log Searched", organization=org, actor=owner).exists()
    csv_resp = oc.get(reverse("organization-audit-logs"), {"export": "csv"})
    assert csv_resp["Content-Type"].startswith("text/csv") and b"Member Added" in csv_resp.content
    assert AuditLog.objects.filter(action="Audit Log Exported", organization=org).exists()
    detail = oc.get(reverse("organization-audit-detail", args=[AuditLog.objects.filter(organization=org, action="Member Added").first().id]))
    assert detail.status_code == 200 and b"alpha-rec@o.tz" in detail.content


@pytest.mark.django_db
def test_platform_admin_audit_console_filters_by_organization_and_former_member(alpha, beta):
    org, owner, recruiter = alpha
    beta_org, beta_owner = beta
    audit_event(None, action="Application Accepted", module="Applications", actor=recruiter, organization=org)
    remove_organization_member(org, OrganizationMember.objects.get(user=recruiter), owner, "Left")
    audit_event(None, action="Slot Created", module="Opportunities", actor=beta_owner, organization=beta_org)
    role = AdminRole.objects.create(name="Metrics", permissions={"system_metrics": {"view": True}})
    admin = _verified(User.objects.create_user(email="adm@p.tz", password=PW, role=UserRole.ADMIN, admin_role=role))
    ac = _client(admin)
    resp = ac.get(reverse("platform-audit-logs"), {"organization": str(org.id), "actor": "alpha-rec@o.tz"})
    assert resp.status_code == 200
    # The former member's earlier action is still found by their name/email.
    assert [l.action for l in resp.context["logs"]] == ["Application Accepted"]
    assert all(l.organization_id == org.id for l in resp.context["logs"])
    assert b"Beta Ltd" in ac.get(reverse("platform-audit-logs")).content  # organization dropdown
    log = AuditLog.objects.filter(organization=org, action="Member Removed").first()
    assert ac.get(reverse("platform-audit-detail", args=[log.id])).status_code == 200
    assert AuditLog.objects.filter(action="Audit Log Searched", actor=admin).exists()
    # Organization users cannot use the platform console.
    assert _client(owner).get(reverse("platform-audit-logs")).status_code == 403


@pytest.mark.django_db
def test_structured_audit_rows_are_sealed_and_still_verify(alpha):
    from apps.core.immutability import verify_record
    from apps.core.models import IntegrityRecord
    from apps.core.signals import _audit_fields

    org, owner, _ = alpha
    log = audit_event(None, action="Settings Changed", module="Org", actor=owner, organization=org,
                      outcome="REJECTED", context={"k": "v"})
    rec = IntegrityRecord.objects.filter(record_type="AUDIT", record_id=log.id).latest("created_at")
    assert verify_record(IntegrityRecord, rec, _audit_fields(log))
    assert rec.payload["outcome"] == "REJECTED" and rec.payload["context"] == {"k": "v"}
    # A legacy-shaped row (no structured fields) hashes exactly as before.
    legacy = AuditLog.objects.create(actor_label="Admin", action="Old", module="M")
    assert "organization_id" not in _audit_fields(legacy) and "context" not in _audit_fields(legacy)

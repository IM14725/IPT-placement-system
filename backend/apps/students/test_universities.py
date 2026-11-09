import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User, UserRole
from apps.locations.models import District, Region, Ward
from apps.students.forms import StudentProfileForm
from apps.students.models import StudentProfile, University, VerificationStatus
from apps.students.universities import (
    MAX_PENDING_SUGGESTIONS_PER_USER,
    SuggestionError,
    check_suggestion,
    seed_universities,
    selectable_universities,
    suggest_university,
)

PW = "Secret123!x"


@pytest.fixture
def student(db):
    return User.objects.create_user(email="s@x.tz", password=PW, role=UserRole.STUDENT)


@pytest.fixture
def udsm(db):
    # The students migration already seeds the reference list, so this exists.
    return University.objects.get_or_create(
        name="University of Dar es Salaam", defaults={"abbreviation": "UDSM"}
    )[0]


_PLACE = {}


@pytest.fixture(autouse=True)
def place(db):
    region = Region.objects.create(name="Dar es Salaam", slug="dar-es-salaam")
    district = District.objects.create(name="Kinondoni", region=region)
    ward = Ward.objects.create(name="Mwananyamala", district=district)
    _PLACE.update(region=str(region.pk), district=str(district.pk), ward=str(ward.pk))
    return _PLACE


def _data(**extra):
    base = {
        **_PLACE,
        "phone": "255712345678",
        "course": "Computer Science",
        "student_id": "",
        "gender": "",
        "education_level": "",
        "academic_year": "",
        "gpa": "",
        "skills": "",
    }
    base.update(extra)
    return base


def test_migration_seeds_reference_list(db):
    assert University.objects.filter(abbreviation="UDSM", verification_status=VerificationStatus.APPROVED).exists()


def test_seed_is_idempotent_and_approved(db):
    University.objects.all().delete()
    first = seed_universities()
    assert first > 50
    assert seed_universities() == 0
    assert not University.objects.exclude(verification_status=VerificationStatus.APPROVED).exists()


def test_university_names_are_unique_case_insensitively(db, udsm):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        University.objects.create(name="university of dar es salaam")


def test_university_is_optional_on_profile(student):
    form = StudentProfileForm(_data(), user=student)
    assert form.is_valid(), form.errors
    profile = form.save(commit=False)
    profile.user = student
    profile.save()
    assert profile.university is None


def test_student_can_pick_existing_university(student, udsm):
    form = StudentProfileForm(_data(university=str(udsm.pk), graduation_year="2027"), user=student)
    assert form.is_valid(), form.errors
    profile = form.save(commit=False)
    profile.user = student
    profile.save()
    assert profile.university == udsm
    assert profile.graduation_year == 2027


def test_phone_is_required_on_profile_and_saved_to_user(student):
    form = StudentProfileForm(_data(phone=""), user=student)
    assert not form.is_valid()
    assert "phone" in form.errors

    form = StudentProfileForm(_data(phone="255712345678"), user=student)
    assert form.is_valid(), form.errors
    form.save(commit=False)
    student.refresh_from_db()
    assert student.phone == "255712345678"


def test_phone_not_required_when_admin_edits(student):
    form = StudentProfileForm(_data(phone=""), user=student, require_phone=False)
    assert form.is_valid(), form.errors


def test_not_listed_creates_pending_suggestion(student):
    form = StudentProfileForm(
        _data(university_not_listed="on", university_suggestion="  Example   University of Tanzania "),
        user=student,
    )
    assert form.is_valid(), form.errors
    profile = form.save(commit=False)
    profile.user = student
    profile.save()
    uni = profile.university
    assert uni.name == "Example University of Tanzania"
    assert uni.verification_status == VerificationStatus.PENDING
    assert uni.suggested_by == student


def test_not_listed_requires_a_name(student):
    form = StudentProfileForm(_data(university_not_listed="on", university_suggestion=""), user=student)
    assert not form.is_valid()
    assert "university_suggestion" in form.errors


def test_not_listed_reuses_existing_record(student, udsm):
    before = University.objects.count()
    form = StudentProfileForm(
        _data(university_not_listed="on", university_suggestion="udsm"), user=student
    )
    assert form.is_valid(), form.errors
    profile = form.save(commit=False)
    profile.user = student
    profile.save()
    assert profile.university == udsm
    assert University.objects.count() == before


def test_suggestion_limit_per_student(student):
    for i in range(MAX_PENDING_SUGGESTIONS_PER_USER):
        suggest_university(student, f"Suggested College {i}")
    with pytest.raises(SuggestionError):
        check_suggestion(student, "One Too Many College")


def test_rejected_university_cannot_be_resuggested(student):
    University.objects.create(name="Bogus Institute", verification_status=VerificationStatus.REJECTED)
    with pytest.raises(SuggestionError):
        check_suggestion(student, "Bogus Institute")


def test_selectable_hides_other_students_pending(student, udsm):
    other = User.objects.create_user(email="o@x.tz", password=PW, role=UserRole.STUDENT)
    mine = suggest_university(student, "My Pending College")
    theirs = suggest_university(other, "Their Pending College")
    names = set(selectable_universities(student).values_list("name", flat=True))
    assert udsm.name in names
    assert mine.name in names
    assert theirs.name not in names


def test_profile_page_requires_phone_end_to_end(student, udsm):
    client = Client()
    client.force_login(student)
    url = reverse("student-profile")

    resp = client.post(url, _data(phone="", university=str(udsm.pk)))
    assert resp.status_code == 200
    assert not StudentProfile.objects.filter(user=student).exists()

    resp = client.post(url, _data(university=str(udsm.pk)))
    assert resp.status_code == 302
    profile = StudentProfile.objects.get(user=student)
    assert profile.university == udsm
    student.refresh_from_db()
    assert student.phone == "255712345678"

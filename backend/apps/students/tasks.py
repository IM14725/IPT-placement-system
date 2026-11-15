from celery import shared_task

MAX_ALERTS_PER_SLOT = 500


@shared_task(name="students.match_saved_searches")
def match_saved_searches(slot_id):
    """Tell students about a newly posted (or reopened) slot they asked to hear about.

    Two reasons a student is told: one of their saved searches ("Notify me") matches the slot, or
    they bookmarked another slot from the same organization. In-app notifications only. A student is
    told at most once per slot, however many times it is paused and reopened.
    """
    from django.db.models import Q
    from django.utils import timezone

    from apps.applications.models import Application
    from apps.notifications.inapp import Notification, NotificationType, notify
    from apps.slots.models import Slot, SlotStatus
    from apps.students.models import SavedSearch, SavedSlot

    slot = (
        Slot.objects.select_related("organization", "district__region").filter(id=slot_id).first()
    )
    if slot is None or slot.status != SlotStatus.OPEN or slot.deadline_passed:
        return {"slot_id": str(slot_id), "notified": 0}
    organization = slot.organization
    if not organization.is_approved or organization.is_suspended:
        return {"slot_id": str(slot_id), "notified": 0}

    # The link is unique per slot, which is how we recognise "already told about this slot".
    link = f"/student/organizations/{organization.id}/?slot={slot.id}"
    applied = set(Application.objects.filter(slot=slot).values_list("student__user_id", flat=True))
    already = set(
        Notification.objects.filter(type=NotificationType.SLOT_ALERT, link=link).values_list("user_id", flat=True)
    )

    recipients = {}  # user_id -> (user, reason)
    matched_search_ids = []
    searches = (
        SavedSearch.objects.filter(is_active=True, student__user__is_active=True)
        .filter(Q(region__isnull=True) | Q(region_id=slot.district.region_id))
        .filter(Q(district__isnull=True) | Q(district_id=slot.district_id))
        .filter(Q(education_level__isnull=True) | Q(education_level=slot.education_level))
        .filter(Q(academic_year__isnull=True) | Q(academic_year=slot.academic_year))
        .select_related("student__user", "region", "district")
    )
    for search in searches.iterator(chunk_size=500):
        if not search.matches(slot):
            continue
        matched_search_ids.append(search.id)
        user = search.student.user
        if user.id not in recipients:
            recipients[user.id] = (user, f"It matches your alert: {search.label}.")

    for saved in (
        SavedSlot.objects.filter(slot__organization=organization, student__user__is_active=True)
        .exclude(slot=slot)
        .select_related("student__user", "slot")
    ):
        user = saved.student.user
        if user.id not in recipients:
            recipients[user.id] = (user, f"You saved '{saved.slot.title}' from the same organization.")

    notified = 0
    for user_id, (user, reason) in recipients.items():
        if user_id in applied or user_id in already or notified >= MAX_ALERTS_PER_SLOT:
            continue
        notify(
            user,
            NotificationType.SLOT_ALERT,
            f"New slot: {slot.title}",
            f"{organization.name} posted '{slot.title}' in {slot.district}. {reason}",
            link=link,
        )
        notified += 1
    if matched_search_ids:
        SavedSearch.objects.filter(id__in=matched_search_ids).update(last_matched_at=timezone.now())
    return {"slot_id": str(slot.id), "notified": notified}

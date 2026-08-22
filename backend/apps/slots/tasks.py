from celery import shared_task


@shared_task(name="slots.refresh_status")
def refresh_status(slot_id):
    from apps.slots.models import Slot

    slot = Slot.objects.get(id=slot_id)
    slot.refresh_status()
    return {"slot_id": slot.id, "status": slot.status, "available": slot.available_count}
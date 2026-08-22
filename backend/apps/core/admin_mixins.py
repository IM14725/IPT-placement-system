from django import forms
from django.contrib import admin
from django.utils import timezone


class VerifyActionForm(forms.Form):
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Required when rejecting.",
    )


class VerificationAdminMixin:
    """Shared approve/reject queue actions with a reason field."""

    actions = ("approve_selected", "reject_selected")
    action_form = VerifyActionForm
    REQUIRED_DOC_TYPES = ()

    def _missing_docs(self, obj):
        from apps.documents.models import Document

        have = set(
            Document.objects.filter(
                owner=obj.user, doc_type__in=self.REQUIRED_DOC_TYPES
            ).values_list("doc_type", flat=True)
        )
        return [d for d in self.REQUIRED_DOC_TYPES if d not in have]

    @admin.action(description="Approve selected (requires verification documents)")
    def approve_selected(self, request, queryset):
        approved = 0
        skipped = 0
        for obj in queryset:
            missing = self._missing_docs(obj)
            if missing:
                skipped += 1
                continue
            obj.verification_status = "APPROVED"
            obj.rejection_reason = ""
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
            obj.save(
                update_fields=[
                    "verification_status",
                    "rejection_reason",
                    "reviewed_by",
                    "reviewed_at",
                    "updated_at",
                ]
            )
            approved += 1
        msg = f"{approved} record(s) approved."
        if skipped:
            msg += f" {skipped} skipped (missing required documents)."
        self.message_user(request, msg)

    @admin.action(description="Reject selected (requires reason)")
    def reject_selected(self, request, queryset):
        reason = request.POST.get("reason", "").strip()
        if not reason:
            self.message_user(request, "A rejection reason is required.", level="ERROR")
            return
        count = queryset.update(
            verification_status="REJECTED",
            rejection_reason=reason,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        self.message_user(request, f"{count} record(s) rejected.")
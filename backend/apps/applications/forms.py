from django import forms
from django.utils.translation import gettext_lazy as _

from apps.documents.validators import validate_upload


class ApplicationLetterForm(forms.Form):
    """Upload the student's university application letter before applying."""

    application_letter = forms.FileField(
        label=_("University Application Letter"),
        help_text=_("The letter from your university requesting the placement (PDF/image/DOC, max 2MB)."),
        validators=[validate_upload],
        widget=forms.FileInput(
            attrs={
                "class": "ipt-file-input",
                "accept": ".pdf,.png,.jpg,.jpeg,.doc,.docx",
            }
        ),
    )
    accept_rules = forms.BooleanField(
        required=True,
        label=_("I understand the fee, the payment deadline and the cancellation rule."),
        error_messages={"required": _("Tick the box to confirm you understand the rules.")},
        widget=forms.CheckboxInput(attrs={"class": "mt-1 rounded border-outline"}),
    )

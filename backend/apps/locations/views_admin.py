"""Platform console: manage regions, districts and wards (the ``locations`` admin module)."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from apps.core.pagination import paginate
from apps.core.views import _require_admin, _write_audit
from apps.locations.models import District, Region, Ward

FIELD_CLASS = (
    "w-full bg-surface-container-lowest text-on-surface font-body-md text-body-md "
    "px-sm py-2 rounded-lg border border-outline-variant "
    "focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
)

KINDS = ("regions", "districts", "wards")


def _clean_name(value):
    return " ".join((value or "").split())


class RegionForm(forms.ModelForm):
    class Meta:
        model = Region
        fields = ("name",)
        widgets = {"name": forms.TextInput(attrs={"class": FIELD_CLASS})}

    def clean_name(self):
        name = _clean_name(self.cleaned_data["name"])
        clash = Region.objects.filter(Q(name__iexact=name) | Q(slug=slugify(name)))
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A region with this name already exists.")
        return name

    def save(self, commit=True):
        self.instance.slug = slugify(self.instance.name)
        return super().save(commit=commit)


class DistrictForm(forms.ModelForm):
    class Meta:
        model = District
        fields = ("name", "region")
        widgets = {
            "name": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "region": forms.Select(attrs={"class": FIELD_CLASS}),
        }

    def clean(self):
        cleaned = super().clean()
        name, region = _clean_name(cleaned.get("name")), cleaned.get("region")
        if name and region:
            clash = District.objects.filter(name__iexact=name, region=region)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error("name", "This region already has a district with this name.")
            cleaned["name"] = name
        return cleaned


class WardForm(forms.ModelForm):
    class Meta:
        model = Ward
        fields = ("name", "district")
        widgets = {
            "name": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "district": forms.Select(attrs={"class": FIELD_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["district"].queryset = District.objects.select_related("region").order_by(
            "region__name", "name"
        )

    def clean(self):
        cleaned = super().clean()
        name, district = _clean_name(cleaned.get("name")), cleaned.get("district")
        if name and district:
            clash = Ward.objects.filter(name__iexact=name, district=district)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error("name", "This district already has a ward with this name.")
            cleaned["name"] = name
        return cleaned


FORMS = {"regions": RegionForm, "districts": DistrictForm, "wards": WardForm}
MODELS = {"regions": Region, "districts": District, "wards": Ward}
LABELS = {"regions": "Region", "districts": "District", "wards": "Ward"}


def _delete_blockers(kind, obj):
    """Human-readable reasons a record cannot be removed (live data still points at it)."""
    from apps.companies.models import Organization
    from apps.slots.models import Slot
    from apps.students.models import StudentProfile, University

    reasons = []

    def add(count, noun):
        if count:
            reasons.append(f"{count} {noun}")

    if kind == "regions":
        add(Slot.objects.filter(district__region=obj).count(), "slot(s)")
        add(StudentProfile.objects.filter(region=obj).count(), "student profile(s)")
        add(Organization.objects.filter(region=obj).count(), "organization(s)")
        add(University.objects.filter(region=obj).count(), "universit(ies)")
        add(obj.districts.count(), "district(s)")
    elif kind == "districts":
        add(Slot.objects.filter(district=obj).count(), "slot(s)")
        add(StudentProfile.objects.filter(district=obj).count(), "student profile(s)")
        add(Organization.objects.filter(district=obj).count(), "organization(s)")
        add(obj.wards.count(), "ward(s)")
    else:
        add(StudentProfile.objects.filter(ward=obj).count(), "student profile(s)")
        add(Organization.objects.filter(ward=obj).count(), "organization(s)")
    return reasons


def _forbidden(request):
    return render(request, "core/forbidden.html", status=403)


def _queryset(kind, q, region_id, district_id):
    if kind == "regions":
        qs = Region.objects.annotate(child_count=Count("districts", distinct=True))
        if q:
            qs = qs.filter(name__icontains=q)
    elif kind == "districts":
        qs = District.objects.select_related("region").annotate(child_count=Count("wards", distinct=True))
        if q:
            qs = qs.filter(name__icontains=q)
        if region_id:
            qs = qs.filter(region_id=region_id)
    else:
        qs = Ward.objects.select_related("district__region")
        if q:
            qs = qs.filter(name__icontains=q)
        if district_id:
            qs = qs.filter(district_id=district_id)
        elif region_id:
            qs = qs.filter(district__region_id=region_id)
    return qs


@login_required
def locations(request):
    """List, add, edit and delete regions, districts and wards."""
    if not _require_admin(request, module="locations", cap="view"):
        return _forbidden(request)

    kind = request.GET.get("kind") or request.POST.get("kind") or "regions"
    if kind not in KINDS:
        kind = "regions"
    model, form_class, label = MODELS[kind], FORMS[kind], LABELS[kind]

    edit_obj = None
    edit_id = request.GET.get("edit")
    if edit_id:
        edit_obj = get_object_or_404(model, pk=edit_id)
    form = form_class(instance=edit_obj)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete":
            if not _require_admin(request, module="locations", cap="delete"):
                return _forbidden(request)
            obj = get_object_or_404(model, pk=request.POST.get("id"))
            blockers = _delete_blockers(kind, obj)
            if blockers:
                messages.error(
                    request,
                    f"{label} {obj.name} can't be deleted while it is used by {', '.join(blockers)}.",
                )
            else:
                name = obj.name
                obj.delete()
                _write_audit(request, f"{label} Deleted", "Locations", f"Deleted {label.lower()} {name}.")
                messages.success(request, f"{label} {name} deleted.")
            return redirect(request.POST.get("next") or f"{request.path}?kind={kind}")

        updating = action == "update"
        if not _require_admin(request, module="locations", cap="edit" if updating else "create"):
            return _forbidden(request)
        instance = get_object_or_404(model, pk=request.POST.get("id")) if updating else None
        form = form_class(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            verb = "Updated" if updating else "Added"
            _write_audit(request, f"{label} {verb}", "Locations", f"{verb} {label.lower()} {obj.name}.")
            messages.success(request, f"{label} {obj.name} {verb.lower()}.")
            return redirect(f"{request.path}?kind={kind}")
        edit_obj = instance

    q = (request.GET.get("q") or "").strip()
    region_id = request.GET.get("region") or ""
    district_id = request.GET.get("district") or ""
    qs = _queryset(kind, q, region_id, district_id)
    page_obj = paginate(qs.order_by("name"), request.GET.get("page"), page_size=25)

    return render(
        request,
        "core/locations.html",
        {
            "kind": kind,
            "label": label,
            "rows": page_obj,
            "page_obj": page_obj,
            "q": q,
            "region_id": region_id,
            "district_id": district_id,
            "form": form,
            "edit_obj": edit_obj,
            "tabs": [
                ("regions", "Regions", Region.objects.count()),
                ("districts", "Districts", District.objects.count()),
                ("wards", "Wards", Ward.objects.count()),
            ],
            "regions": Region.objects.order_by("name"),
            "districts": (
                District.objects.filter(region_id=region_id).order_by("name") if region_id else District.objects.none()
            ),
        },
    )

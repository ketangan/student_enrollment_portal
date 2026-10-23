import hashlib
import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.forms.models import model_to_dict
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from . import inventory
from .access import require_access, require_location_access, staff_locations, location_restricted
from .editing import protect_edit
from .inventory_forms import CatalogForm, CatalogGroupForm, CatalogColorForm, ChoiceOptionForm, CatalogVariantForm, StockCountForm, MaterialForm, DesignForm, recipe_forms
from .models import ChoiceGroup, ChoiceOption, InventoryCatalog, Location, SchedulingProfile, CatalogVariant, StockLevel, Material
from .setup import _audit, records
from .views import _context


@never_cache
@login_required
@require_GET
def settings_home(request, school_slug):
    profile, role = require_location_access(request.user, school_slug)
    context = _context(request, profile, role, "settings")
    location = context["selected_location"]
    workshop = None
    if "workshop" in request.GET:
        try:
            workshop_id = forms.IntegerField(min_value=1, max_value=2**63 - 1).clean(request.GET["workshop"])
        except ValidationError as exc:
            raise Http404 from exc
        workshop = get_object_or_404(records(profile, "workshops"), pk=workshop_id)
        if not location or workshop.location_id != location.pk:
            context["location_filter"].add_error("location", "Choose this workshop's location to view its stock.")
    query = request.GET.get("q", "").strip()[:100]
    rows = inventory.stock_rows(profile, location, workshop.inventory_catalog if workshop else None) if (
        context["location_filter"].is_valid() and (not workshop or workshop.inventory_catalog_id)
    ) else []
    if query:
        rows = [row for row in rows if query.casefold() in row["level"].material.name.casefold()]
    page = Paginator(rows, 25).get_page(request.GET.get("page"))
    filters = request.GET.copy()
    filters.pop("page", None)
    context.update(catalogs=profile.inventory_catalogs.none() if location_restricted(profile) else profile.inventory_catalogs.annotate(item_count=Count("items")).prefetch_related("offerings__location"),
        locations=staff_locations(profile), selected_location=location,
        stock=page, page_obj=page, query=query, filters=filters.urlencode(), selected_workshop=workshop)
    # Choosing another studio leaves the individual-workshop view.
    context["location_query_fields"] = [(key, value) for key, value in context["location_query_fields"] if key != "workshop"]
    return render(request, "scheduling/settings/inventory.html", context, status=200 if context["location_filter"].is_valid() else 400)


@never_cache
@login_required
@require_GET
def catalog_detail(request, school_slug, catalog_pk):
    profile, role = require_access(request.user, school_slug)
    catalog = get_object_or_404(InventoryCatalog, profile=profile, pk=catalog_pk)
    context = _context(request, profile, role, "catalog")
    query = request.GET.get("q", "").strip()[:100]
    items = catalog.items.prefetch_related("requirements__material")
    items = list(items)
    for variant in items:
        requirements = list(variant.requirements.all())
        variant.recipe_ready = bool(requirements) and all(row.material.is_active for row in requirements)
    context.update(catalog=catalog, groups=catalog.choice_groups.prefetch_related("options"), query=query)
    design_group = catalog.choice_groups.filter(tracks_stock=True, is_active=True).first()
    designs = []
    if design_group:
        for design in design_group.options.all():
            if query and query.casefold() not in design.label.casefold():
                continue
            variants = [v for v in items if design.pk in v.option_ids]
            previews = profile.school.feature_flags.get("booking_demo_inventory", {}).get("design_previews", {})
            designs.append({"option": design, "variants": variants, "preview": previews.get(str(design.pk)),
                "reference_image": design_group.reference_image})
    page = Paginator(designs, 12).get_page(request.GET.get("page"))
    context.update(designs=page, page_obj=page, design_group=design_group)
    return render(request, "scheduling/settings/catalog.html", context)


def build_form(profile, kind, catalog_pk=None, pk=None, group_pk=None, data=None):
    catalog = get_object_or_404(InventoryCatalog, profile=profile, pk=catalog_pk) if catalog_pk else None
    if kind == "catalog":
        form = CatalogForm(data, instance=catalog or InventoryCatalog(profile=profile))
    elif kind == "group" and catalog:
        instance = get_object_or_404(ChoiceGroup, pk=pk, catalog=catalog) if pk else ChoiceGroup(catalog=catalog)
        form = CatalogGroupForm(data, instance=instance)
    elif kind == "option" and catalog:
        group = get_object_or_404(ChoiceGroup, pk=group_pk, catalog=catalog)
        instance = get_object_or_404(ChoiceOption, pk=pk, group=group) if pk else ChoiceOption(group=group)
        # The second recipe dimension is the color axis, as in DesignForm.
        color_group = catalog.choice_groups.filter(tracks_stock=True)[1:2].first()
        form_class = CatalogColorForm if color_group and group.pk == color_group.pk else ChoiceOptionForm
        form = form_class(data, instance=instance)
    elif kind == "item" and catalog:
        instance = get_object_or_404(CatalogVariant, pk=pk, catalog=catalog) if pk else CatalogVariant(catalog=catalog)
        form = CatalogVariantForm(data, instance=instance)
    elif kind == "material":
        instance = get_object_or_404(Material, pk=pk, profile=profile) if pk else Material(profile=profile)
        form = MaterialForm(data, instance=instance)
    else:
        raise Http404
    return catalog, protect_edit(form, profile)


@transaction.atomic
def save(request, slug, **kwargs):
    profile, _ = require_access(request.user, slug, "setup.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_access(request.user, slug, "setup.edit")
    catalog, form = build_form(profile, data=request.POST, **kwargs)
    before = model_to_dict(form.instance) if form.instance.pk else {}
    if form.is_valid():
        try:
            form.instance.full_clean()
        except ValidationError as exc:
            form.add_error(None, exc.messages)
        else:
            item = form.save()
            if isinstance(item, Material):
                for location in profile.locations.all():
                    StockLevel.objects.get_or_create(material=item, location=location)
            if isinstance(item, InventoryCatalog) and not before:
                for position, name in enumerate(("Design", "Color")):
                    ChoiceGroup.objects.create(catalog=item, name=name, position=position, tracks_stock=True)
            _audit(request.user, profile, item, before, "change" if before else "add")
    return catalog, form


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def edit(request, school_slug, kind, catalog_pk=None, pk=None, group_pk=None):
    profile, role = require_access(request.user, school_slug, "setup.edit")
    args = dict(kind=kind, catalog_pk=catalog_pk, pk=pk, group_pk=group_pk)
    if request.method == "POST":
        catalog, form = save(request, school_slug, **args)
        if form.is_valid():
            messages.success(request, "Changes saved.")
            if kind == "material":
                return redirect("scheduling:settings", school_slug=school_slug)
            if kind in {"group", "option"}:
                return redirect("scheduling:catalog_settings", school_slug=school_slug, catalog_pk=catalog.pk)
            return redirect("scheduling:inventory_catalog", school_slug=school_slug,
                            catalog_pk=catalog.pk if catalog else form.instance.pk)
    else:
        catalog, form = build_form(profile, **args)
    context = _context(request, profile, role, "settings" if kind == "material" else "catalog")
    context.update(form=form, catalog=catalog, title={"catalog": "Catalog", "group": "Choice group", "option": "Option", "item": "Design / color", "material": "Material"}[kind])
    if getattr(form, "is_color_option", False):
        context["title"] = "Edit color" if pk else "Add color"
    if kind in {"group", "option"}:
        context["catalog_return_url"] = reverse("scheduling:catalog_settings", kwargs={"school_slug": school_slug, "catalog_pk": catalog.pk})
    return render(request, "scheduling/settings/form.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def stock_count(request, school_slug, pk):
    profile, role = require_location_access(request.user, school_slug, "setup.edit")
    level = get_object_or_404(StockLevel.objects.select_related("material", "location"), pk=pk, location__in=staff_locations(profile), material__profile=profile)
    form = StockCountForm(request.POST if request.method == "POST" else None,
                          initial={"mode": "receive", "previous": level.on_hand,
                                   "low_stock_threshold": level.low_stock_threshold, "previous_threshold": level.low_stock_threshold})
    if request.method == "POST" and form.is_valid():
        try:
            values = dict(form.cleaned_data)
            if values.pop("mode") == "receive":
                values["on_hand"] += values["previous"]
            inventory.adjust(request.user, school_slug, pk, **values)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Stock count saved.")
            return redirect(f'{reverse("scheduling:settings", kwargs={"school_slug": school_slug})}?location={level.location_id}')
    context = _context(request, profile, role, "settings")
    context.update(form=form, title=f"{level.material.name} / {level.location.name}", level=level,
        reserved=inventory.reserved(level), history=level.adjustments.select_related("actor")[:25],
        usage=inventory.claims().filter(level=level).select_related("reservation")[:50],
        recipes=level.material.recipes.none() if location_restricted(profile) else level.material.recipes.select_related("variant__catalog").prefetch_related("variant__catalog__offerings__location"))
    return render(request, "scheduling/settings/form.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_GET
def catalogs(request, school_slug):
    profile, role = require_access(request.user, school_slug)
    context = _context(request, profile, role, "catalog")
    context["catalogs"] = profile.inventory_catalogs.prefetch_related("offerings__location", "choice_groups__options")
    return render(request, "scheduling/settings/catalogs.html", context)


@never_cache
@login_required
@require_GET
def catalog_settings(request, school_slug, catalog_pk):
    profile, role = require_access(request.user, school_slug)
    catalog = get_object_or_404(InventoryCatalog, profile=profile, pk=catalog_pk)
    context = _context(request, profile, role, "catalog")
    context.update(catalog=catalog, groups=catalog.choice_groups.prefetch_related("options"),
        design_group=catalog.choice_groups.filter(tracks_stock=True, is_active=True).first())
    return render(request, "scheduling/settings/catalog_settings.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def design(request, school_slug, catalog_pk, pk=None):
    profile, role = require_access(request.user, school_slug, "setup.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, role = require_access(request.user, school_slug, "setup.edit")
    catalog = get_object_or_404(InventoryCatalog, profile=profile, pk=catalog_pk)
    group = catalog.choice_groups.filter(tracks_stock=True, is_active=True).first()
    if not group:
        raise Http404
    instance = get_object_or_404(ChoiceOption, group=group, pk=pk) if pk else ChoiceOption(group=group)
    form = protect_edit(DesignForm(request.POST if request.method == "POST" else None, instance=instance, catalog=catalog), profile)
    if instance.pk:
        from .editing import EditRevisionField
        state = [[v.pk, v.is_active, v.option_ids, v.image_url] for v in catalog.items.order_by("pk") if instance.pk in v.option_ids]
        form.fields["colors_revision"] = EditRevisionField({"profile": profile.pk, "design": instance.pk,
            "variants": hashlib.sha256(json.dumps(state).encode()).hexdigest()})
    before = model_to_dict(instance) if pk else {}
    if request.method == "POST" and form.is_valid():
        option = form.save()
        chosen = set()
        for color in form.cleaned_data["colors"]:
            ids = sorted([option.pk, color.pk])
            variant, created = CatalogVariant.objects.get_or_create(catalog=catalog, choice_key=":".join(map(str, ids)),
                defaults={"option_ids": ids, "name": f"{option.label} / {color.label}"})
            variant_before = {} if created else model_to_dict(variant)
            variant.name, variant.is_active = f"{option.label} / {color.label}", option.is_active
            variant.image_url = form.cleaned_data[f"image_{color.pk}"]
            variant.save(update_fields=["name", "is_active", "image_url"])
            if variant_before != model_to_dict(variant):
                _audit(request.user, profile, variant, variant_before, "add" if created else "change")
            chosen.add(variant.pk)
        for variant in catalog.items.all():
            if option.pk in variant.option_ids and variant.pk not in chosen and variant.is_active:
                variant_before = model_to_dict(variant)
                variant.is_active = False
                variant.save(update_fields=["is_active"])
                _audit(request.user, profile, variant, variant_before, "change")
        _audit(request.user, profile, option, before, "change" if pk else "add")
        messages.success(request, "Design saved. Set the material recipe for each color before accepting bookings.")
        return redirect("scheduling:inventory_catalog", school_slug=school_slug, catalog_pk=catalog.pk)
    context = _context(request, profile, role, "catalog")
    context.update(form=form, catalog=catalog, title="Edit design" if pk else "Add design")
    return render(request, "scheduling/settings/form.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def recipe(request, school_slug, catalog_pk, pk):
    from .editing import EditRevisionField
    profile, role = require_access(request.user, school_slug, "setup.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, role = require_access(request.user, school_slug, "setup.edit")
    catalog = get_object_or_404(InventoryCatalog, profile=profile, pk=catalog_pk)
    variant = get_object_or_404(CatalogVariant, catalog=catalog, pk=pk)
    rows = recipe_forms(variant, profile, request.POST if request.method == "POST" else None)
    before = list(variant.requirements.values("id", "material_id", "quantity"))
    revision = forms.Form(request.POST if request.method == "POST" else None)
    revision.fields["recipe_revision"] = EditRevisionField({"profile": profile.pk, "variant": variant.pk,
        "rows": [[r["id"], r["material_id"], str(r["quantity"])] for r in before]})
    if request.method == "POST" and rows.is_valid() and revision.is_valid():
        rows.save()
        from core.models import AdminAuditLog
        before_values = [[r["material_id"], str(r["quantity"])] for r in before]
        after_values = [[r.material_id, str(r.quantity)] for r in variant.requirements.all()]
        AdminAuditLog.objects.create(actor=request.user, action="change", model_label="scheduling.catalogvariant",
            object_id=str(variant.pk), object_repr=variant.name,
            changes={"recipe": {"from": before_values, "to": after_values}},
            extra={"scheduling_profile_id": profile.pk, "school_slug": school_slug})
        messages.success(request, "Recipe saved. Existing reservations keep their original material amounts.")
        return redirect("scheduling:inventory_catalog", school_slug=school_slug, catalog_pk=catalog.pk)
    design_id = next((i for i in variant.option_ids if ChoiceOption.objects.filter(pk=i, group=catalog.choice_groups.filter(tracks_stock=True).first()).exists()), None)
    siblings = [v for v in catalog.items.all() if design_id in v.option_ids and v.is_active]
    context = _context(request, profile, role, "catalog")
    context.update(catalog=catalog, variant=variant, rows=rows, revision=revision, siblings=siblings)
    return render(request, "scheduling/settings/recipe.html", context, status=400 if request.method == "POST" else 200)

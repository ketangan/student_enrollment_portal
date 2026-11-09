"""Staff location preference, always bounded by membership permissions."""

from django import forms

from .models import Location
from .access import staff_locations, scope_locations, location_restricted
from .school_navigation import navigation_policy


class LocationFilterForm(forms.Form):
    location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False, empty_label="All locations",
        widget=forms.Select(attrs={"class": "dash-select"}),
    )

    def __init__(self, data, *, profile):
        super().__init__(data)
        self.profile = profile
        if location_restricted(profile):
            self.fields["location"].empty_label = "All assigned locations"
        self.fields["location"].queryset = staff_locations(profile).order_by("name", "pk")
        # Hide presentation only; submitted/stale locations still pass the same
        # membership-scoped validation. A second location restores the selector.
        self.show_location_filter = not (
            navigation_policy(profile.school)["single_location"]
            and self.fields["location"].queryset.count() == 1
        )
        if not self.show_location_filter:
            self.fields["location"].widget = forms.HiddenInput()

    @property
    def selected(self):
        return self.cleaned_data.get("location") if self.is_valid() else None

    def filter(self, items, lookup):
        if not self.is_valid():
            return items.none()
        items = scope_locations(items, self.profile, lookup)
        return items.filter(**{lookup: self.selected.pk}) if self.selected else items


def staff_location(request, profile):
    cache = getattr(request, "_booking_location_context", {})
    if profile.pk not in cache:
        key = f"booking_location:{profile.pk}"
        data = request.GET.copy()
        if "location" not in data:
            data["location"] = request.session.get(key, "")
        form = LocationFilterForm(data, profile=profile)
        form.fields["location"].widget.attrs["id"] = "booking-location"
        # Invalid/stale selections stay invalid until explicitly cleared. They
        # must not silently widen a filtered list to every studio.
        if form.is_valid() and "location" in request.GET:
            value = str(form.selected.pk) if form.selected else ""
            if request.session.get(key, "") != value:
                request.session[key] = value
        cache[profile.pk] = {"location_filter": form,
            "dashboard_location_form": form if form.show_location_filter else None,
            "show_location_filter": form.show_location_filter,
            "selected_location": form.selected, "location_query": data}
        request._booking_location_context = cache
    return cache[profile.pk]

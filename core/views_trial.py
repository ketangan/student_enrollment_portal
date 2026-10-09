from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.forms_trial import TrialActivationForm
from core.models import School
from core.services.trial_activation import start_customer_trial
from core.views_ops import ops_required


@ops_required
@require_http_methods(["GET", "POST"])
def trial_activation_view(request, slug):
    school = get_object_or_404(School, slug=slug)
    form = TrialActivationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            started = start_customer_trial(
                school_id=school.pk, actor=request.user,
                notice_sent_at=form.cleaned_data["notice_sent_at"],
                notice_reference=form.cleaned_data["notice_reference"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Customer trial started." if started else "Trial already started; dates unchanged.")
            return redirect("ops_school_detail", slug=slug)
    return render(request, "ops/trial_activation.html", {
        "active_nav": "schools", "school": school, "form": form,
    })

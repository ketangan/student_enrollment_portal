from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from core.payments.models import PaymentOrder
from .access import get_staff_reservation, require_location_access
from .booking_documents import filename, snapshot
from .models import SchedulingProfile


@never_cache
@login_required
@require_GET
@transaction.atomic
def booking_document(request, school_slug, public_id, kind):
    profile, _ = require_location_access(request.user, school_slug, capability="reservations")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(request.user, school_slug, capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    item.order = PaymentOrder.objects.select_for_update().get(pk=item.order_id)
    try:
        document = snapshot(item, kind)
    except ValidationError as exc:
        raise Http404 from exc
    if request.GET.get("format") == "pdf":
        from .booking_document_pdf import render_pdf
        response = HttpResponse(render_pdf(document), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename(document)}"'
    else:
        response = render(request, "scheduling/booking_document.html", {"document": document, "item": item, "business": profile.school})
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response

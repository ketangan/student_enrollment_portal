from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import School
from core.services.billing_stripe import construct_application_fee_event
from core.services.parent_payment_events import notify_application_payment


@csrf_exempt
@require_POST
def application_payment_webhook(request, school_slug):
    school = get_object_or_404(School, slug=school_slug)
    if len(request.body) > 262144:
        return HttpResponse(status=400)
    try:
        event = construct_application_fee_event(school, request.body, request.headers.get("Stripe-Signature", ""))
        notify_application_payment(school, event)
    except ValidationError:
        return HttpResponse(status=400)
    return HttpResponse(status=200)

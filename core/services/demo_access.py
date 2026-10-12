"""Existing magic links, scoped to an active account and its intended workspace."""
from django.db import transaction
from django.utils import timezone

from core.models import DemoAccessToken, SchoolAdminMembership
from scheduling.models import SchedulingMembership


def eligible_members(school, destination):
    if destination == "enrollment":
        members = SchoolAdminMembership.objects.filter(school=school)
    elif destination == "scheduling":
        members = SchedulingMembership.objects.filter(profile__school=school, profile__enabled=True)
    else:
        return SchoolAdminMembership.objects.none()
    return members.filter(is_active=True, user__is_active=True, user__is_superuser=False).select_related("user").order_by("pk")


def demo_only_account(user, school):
    # A shareable prospect link must never authenticate an account for another business.
    return not (SchoolAdminMembership.objects.filter(user=user).exclude(school=school).exists()
        or SchedulingMembership.objects.filter(user=user).exclude(profile__school=school).exists())


def resolve_recipient(token):
    if not token.school.is_active:
        return None
    members = eligible_members(token.school, token.destination)
    if token.recipient_id:
        members = members.filter(user_id=token.recipient_id)
    for member in members:
        if token.purpose != DemoAccessToken.PURPOSE_DEMO or demo_only_account(member.user, token.school):
            return member.user
    return None


@transaction.atomic
def generate_demo_link(school, actor, destination=None):
    school = type(school).objects.select_for_update().get(pk=school.pk)
    if not school.is_demo or not school.is_active:
        raise ValueError("Demo login links require an active demo school.")
    if destination is None:
        destination = "enrollment" if eligible_members(school, "enrollment").exists() else "scheduling"
    token = DemoAccessToken(school=school, destination=destination, created_by=actor,
        expires_at=timezone.now() + timezone.timedelta(days=14))
    user = resolve_recipient(token)
    if user is None:
        raise ValueError("Assign an active, school-only administrator before generating a demo link.")
    token.recipient = user
    # A replacement revokes older demo links, but never real onboarding links.
    DemoAccessToken.objects.filter(school=school, purpose=DemoAccessToken.PURPOSE_DEMO).update(expires_at=timezone.now())
    token.save()
    return token

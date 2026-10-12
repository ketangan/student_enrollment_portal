from django import template
from django.urls import reverse

register = template.Library()


@register.inclusion_tag("partials/account_menu.html", takes_context=True)
def account_menu(context):
    request = context["request"]
    school = context.get("school")
    business = context.get("business")
    links = []
    password_url = ""
    if business:
        from scheduling.navigation import enrollment_url
        password_url = reverse("scheduling:password_change", kwargs={"school_slug": business.slug})
        links.append({"url": reverse("scheduling:index"), "label": "Workspaces"})
        enrollment_home = enrollment_url(request.user, context.get("profile"))
        if enrollment_home:
            links.append({"url": enrollment_home, "label": "Enrollment admin"})
        if context.get("experience_demo_available"):
            from scheduling.experience_demo.config import customer_demo_url
            links.append({"url": customer_demo_url(context["profile"]), "label": "Customer demo"})
    elif school and not context.get("billing_only"):
        password_url = reverse("school_password_change", kwargs={"school_slug": school.slug})
    return {"request": request, "csrf_token": context.get("csrf_token"),
        "initial": (request.user.get_full_name() or request.user.get_username())[:1].upper(),
        "password_url": password_url, "workspace_links": links}

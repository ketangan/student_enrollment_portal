"""
Demo views — serve school-specific integration demo pages.

URL structure:
  /demo/<demo-slug>/               → index listing all demo options
  /demo/<demo-slug>/<demo-name>/   → individual demo page

No authentication required (these are public sales demos).
New school demo = add an entry to DEMO_REGISTRY + templates in templates/demo/<dir>/.
"""
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

DEMO_REGISTRY = {
    "ymla-demo": {
        "school_slug": "young-minds-la",
        "template_dir": "demo/ymla",
        "demos": ["dedicated-page", "modal", "bottom-section", "link-out", "standalone-form"],
    },
}


def demo_index(request, demo_slug):
    config = DEMO_REGISTRY.get(demo_slug)
    if not config:
        raise Http404
    return render(request, f"{config['template_dir']}/index.html", {
        "demo_slug": demo_slug,
        "base_url": request.build_absolute_uri(f"/demo/{demo_slug}/"),
    })


def demo_detail(request, demo_slug, demo_name):
    config = DEMO_REGISTRY.get(demo_slug)
    if not config or demo_name not in config["demos"]:
        raise Http404
    school_slug = config["school_slug"]
    form_url = request.build_absolute_uri(
        reverse("apply", kwargs={"school_slug": school_slug})
    )
    return render(request, f"{config['template_dir']}/{demo_name}.html", {
        "form_url": form_url,
        "embed_form_url": form_url + "?embed=1",
        "demo_slug": demo_slug,
    })

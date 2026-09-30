# Plushumi Booking Storyboard

Public demo route: `/demo/plushumi/`. A screen can be selected with `?screen=0`
through `?screen=5`. Intended sharing URL: https://demo.mypontora.com/demo/plushumi/.

This is a static sales walkthrough, not a booking service. It does not create a
school, collect payment, submit forms, send email, or connect to Wix or Notion.
The staff screen is a proposed preparation layout, not the customer's Notion UI.
Screen 3 is an alternative to screen 2, not an additional customer step.

The existing demo registry serves the standalone index. No new view, enrollment
route, setting, model, or migration is required. The empty `demos` list deliberately
prevents enrollment-form detail routes. The page is public but marked noindex;
noindex is not access control. It contains fictional customer details.

Template: `templates/demo/plushumi/index.html`
Assets: `static/demo/plushumi/assets/`
Regression tests: `core/tests/test_plushumi_demo.py`

Deploy with the application's normal static-file collection and web deployment.
Verify the public URL and all six screens before sharing. Changes keep the same URL.

Brand/product photo sources:
- https://www.theplushieworkshop.com/
- https://www.theplushieworkshop.com/product-page/plushumi-plushie
- https://www.theplushieworkshop.com/plushie-making-workshop-la-pasadena

Plus Jakarta Sans and Lucide are bundled locally so the demo does not depend on
external font or script CDNs. Photo assets and logo belong to The Plushie Workshop.

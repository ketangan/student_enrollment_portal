from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from scheduling import customer_experience
from scheduling.tests.test_inventory import stock

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('party', [False, True])
@pytest.mark.parametrize('setting,visible', [(None, True), (True, True), (False, False)])
def test_customer_footer_attribution_is_scoped(stock, monkeypatch, party, setting, visible):
    policy = {} if setting is None else {'show_powered_by': setting}
    monkeypatch.setattr(customer_experience, 'load_school_config', lambda slug:
        SimpleNamespace(raw={'scheduling': {'customer_experience': policy}}))
    html = render_to_string('scheduling/experience/base.html', {
        'business': stock.school, 'demo': {'name': 'Studio'}, 'party_experience': party,
    })
    assert ('Powered by Pontora' in html) == visible
    assert ('href="https://mypontora.com/"' in html) == visible
    assert 'Pontora x' not in html
    assert 'Back to parties' in html if party else 'Back to workshops' in html

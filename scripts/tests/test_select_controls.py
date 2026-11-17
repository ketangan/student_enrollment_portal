from pathlib import Path

import pytest
from django.contrib.staticfiles import finders


@pytest.mark.parametrize('entry,relative', [
    ('forms.css', 'select-controls.css'),
    ('admin/dashboard.css', '../select-controls.css'),
    ('admin/admin_global.css', '../select-controls.css'),
    ('scheduling/workspace.css', '../select-controls.css'),
    ('scheduling/experience.css', '../select-controls.css'),
])
def test_shared_select_style_is_loaded_before_other_rules(entry, relative):
    source = Path(finders.find(entry))
    assert source.read_text().startswith(f'@import url("{relative}");')
    assert (source.parent / relative).resolve() == Path(finders.find('select-controls.css')).resolve()


def test_arrow_rule_excludes_native_listboxes_and_supports_high_contrast():
    css = Path(finders.find('select-controls.css')).read_text()
    assert 'select:not([multiple]):is(:not([size]), [size="0"], [size="1"])' in css
    assert 'right 12px center' in css
    assert 'padding-right: 36px' in css
    assert '@media (forced-colors: active)' in css
    assert 'appearance: auto !important' in css

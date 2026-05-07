"""
views.py — backward-compat re-export facade.
Actual implementations live in views_public, views_school_common,
views_school_dashboard, views_school_submissions, views_school_leads.
"""
from core.views_public import *  # noqa: F401,F403
from core.views_school_common import *  # noqa: F401,F403
from core.views_school_dashboard import *  # noqa: F401,F403
from core.views_school_submissions import *  # noqa: F401,F403
from core.views_school_leads import *  # noqa: F401,F403

# Explicitly re-export private helpers that tests import directly from core.views.
from core.views_school_common import (  # noqa: F401
    _can_view_school_admin_page,
    _build_submission_row,
    _build_lead_row,
)

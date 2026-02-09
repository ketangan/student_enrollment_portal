from __future__ import annotations

from django import template
from django.contrib.admin.views.main import ChangeList
from django.contrib.admin.templatetags.admin_list import PAGE_VAR
from django.utils.html import format_html
from django.utils.safestring import SafeText, mark_safe

# Import the upstream Jazzmin tag library and re-export everything,
# then override the one tag that breaks on newer Django.
from jazzmin.templatetags import jazzmin as upstream_jazzmin

register = template.Library()

# Re-export upstream tags/filters so Jazzmin templates keep working.
register.tags.update(upstream_jazzmin.register.tags)
register.filters.update(upstream_jazzmin.register.filters)


@register.simple_tag
def jazzmin_paginator_number(change_list: ChangeList, i: int) -> SafeText:
    """Generate an individual page index link in a paginated list.

    Jazzmin's upstream implementation returns `format_html(html_str)` with no
    args/kwargs, which raises `TypeError: args or kwargs must be provided` on
    Django 6+.

    We keep Jazzmin's HTML output but wrap it safely.
    """

    html_str = ""
    start = i == 1
    end = i == change_list.paginator.num_pages
    spacer = i in (".", "…")
    current_page = i == change_list.page_num

    if start:
        link = change_list.get_query_string({PAGE_VAR: change_list.page_num - 1}) if change_list.page_num > 1 else "#"
        html_str += """
        <li class=\"page-item previous {disabled}\">
            <a class=\"page-link\" href=\"{link}\" data-dt-idx=\"0\" tabindex=\"0\">«</a>
        </li>
        """.format(link=link, disabled="disabled" if link == "#" else "")

    if current_page:
        html_str += """
        <li class=\"page-item active\">
            <a class=\"page-link\" href=\"javascript:void(0);\" data-dt-idx=\"3\" tabindex=\"0\">{num}</a>
        </li>
        """.format(num=i)
    elif spacer:
        html_str += """
        <li class=\"page-item\">
            <a class=\"page-link\" href=\"javascript:void(0);\" data-dt-idx=\"3\" tabindex=\"0\">… </a>
        </li>
        """
    else:
        query_string = change_list.get_query_string({PAGE_VAR: i})
        end_class = "end" if end else ""
        html_str += """
            <li class=\"page-item\">
            <a href=\"{query_string}\" class=\"page-link {end}\" data-dt-idx=\"3\" tabindex=\"0\">{num}</a>
            </li>
        """.format(num=i, query_string=query_string, end=end_class)

    if end:
        link = change_list.get_query_string({PAGE_VAR: change_list.page_num + 1}) if change_list.page_num < i else "#"
        html_str += """
        <li class=\"page-item next {disabled}\">
            <a class=\"page-link\" href=\"{link}\" data-dt-idx=\"7\" tabindex=\"0\">»</a>
        </li>
        """.format(link=link, disabled="disabled" if link == "#" else "")

    # Use format_html with an argument to satisfy Django's requirement.
    return format_html("{}", mark_safe(html_str))

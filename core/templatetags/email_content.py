from django import template
from core.services.email_content import clean_html

register = template.Library()
register.filter("sanitize_email", clean_html)

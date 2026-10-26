"""Versioned school guides, served through scoped download views."""
import hashlib
from pathlib import Path

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from pypdf import PdfReader

from .access import require_access
from .models import DesignGuide, SchedulingProfile

MAX_GUIDE_BYTES = 5 * 1024 * 1024


def uploads_enabled():
    return settings.DEBUG or settings.BOOKINGS_GUIDE_STORAGE_READY


class GuideForm(forms.Form):
    current_version = forms.IntegerField(widget=forms.HiddenInput, min_value=0)
    file = forms.FileField(label="Design guide", widget=forms.FileInput(attrs={"accept": ".pdf,application/pdf"}),
        help_text="PDF, up to 5 MB.")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not uploads_enabled():
            raise ValidationError("Upload storage has not been enabled. Contact Pontora.")
        if Path(upload.name).suffix.lower() != ".pdf" or not 0 < upload.size <= MAX_GUIDE_BYTES:
            raise ValidationError("Choose a PDF no larger than 5 MB.")
        try:
            reader = PdfReader(upload, strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 25:
                raise ValueError
            # Guides are static documents, not forms or executable attachments.
            root = reader.root_object
            if any(key in root for key in ("/OpenAction", "/AA", "/AcroForm")):
                raise ValueError
            names = root.get("/Names", {})
            if hasattr(names, "get_object"):
                names = names.get_object()
            if any(key in names for key in ("/JavaScript", "/EmbeddedFiles")):
                raise ValueError
            for page in reader.pages:
                if page.get("/AA") or page.get("/Annots"):
                    raise ValueError
        except Exception:
            raise ValidationError("Choose a readable, unencrypted PDF without forms, annotations or attachments.")
        finally:
            upload.seek(0)
        return upload


def replace(user, slug, form):
    """Serialize replacements; a stale browser must not overwrite a newer upload."""
    stored = None
    try:
        with transaction.atomic():
            profile, _ = require_access(user, slug, "setup.edit", capability="workshops")
            SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
            require_access(user, slug, "setup.edit", capability="workshops")
            current = profile.design_guides.first()
            if form.cleaned_data["current_version"] != (current.pk if current else 0):
                raise ValidationError("The guide changed in another window. Reload before replacing it.")
            upload = form.cleaned_data["file"]
            checksum = hashlib.sha256(upload.read()).hexdigest()
            upload.seek(0)
            if current and checksum == current.checksum and current.file.storage.exists(current.file.name):
                return current
            guide = DesignGuide(profile=profile, original_name=Path(upload.name).name[:200],
                checksum=checksum, uploaded_by=user)
            guide.file.save("guide.pdf", upload, save=False)
            stored = guide.file
            guide.save()
            from .lessons import audit
            audit(user, profile, guide, "add", {"action": "design_guide_uploaded", "checksum": checksum})
            return guide
    except Exception:
        if stored:
            stored.storage.delete(stored.name)
        raise

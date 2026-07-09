"""Controller for the *Duplicate Guard Settings* single DocType."""

import frappe
from frappe import _
from frappe.model.document import Document


class DuplicateGuardSettings(Document):
    """Validates configuration and keeps derived caches fresh.

    A *Single* DocType stores exactly one record of settings for the whole
    site. This controller runs whenever an administrator saves those settings.
    """

    def validate(self):
        self._validate_phone_config()
        self._normalise_text_lists()

    def _validate_phone_config(self):
        """Ensure the phone-normalization parameters are sane."""
        if self.national_number_length is None or int(self.national_number_length) <= 0:
            frappe.throw(_("National Number Length must be a positive integer."))

        code = (self.default_country_code or "").strip()
        if code and not code.isdigit():
            frappe.throw(_("Default Country Code must contain digits only (no '+')."))

        region = (self.default_region or "").strip()
        if region and not (len(region) == 2 and region.isalpha()):
            frappe.throw(
                _("Default Region must be a 2-letter ISO code, e.g. IN, US, GB.")
            )

    def _normalise_text_lists(self):
        """Trim blank lines from the multi-line text settings."""
        if self.guarded_doctypes:
            lines = [ln.strip() for ln in self.guarded_doctypes.splitlines() if ln.strip()]
            self.guarded_doctypes = "\n".join(lines)

    def on_update(self):
        """Clear engine caches so new settings take effect immediately."""
        # The field-map cache depends on the guarded-doctypes list and the
        # name-field overrides, so clear it whenever settings change.
        from duplicate_guard.core.metadata import clear_field_cache

        clear_field_cache()
        # Single DocTypes are cached by Frappe; drop that cache too.
        frappe.clear_document_cache("Duplicate Guard Settings", "Duplicate Guard Settings")

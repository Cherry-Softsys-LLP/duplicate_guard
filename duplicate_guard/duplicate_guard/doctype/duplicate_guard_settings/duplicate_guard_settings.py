"""Controller for the *Duplicate Guard Settings* single DocType."""

import frappe
from frappe import _
from frappe.model.document import Document

# Statuses ERPNext's Employee DocType actually offers. Used to catch typos in the
# Active Employee Statuses setting, where a typo silently disables the rule.
_KNOWN_EMPLOYEE_STATUSES = {"Active", "Inactive", "Suspended", "Left"}

# The multi-line "DocType: field, field" settings, validated with one rule.
_FIELD_MAP_SETTINGS = (
    "name_field_overrides",
    "phone_field_overrides",
    "email_field_overrides",
    "ignored_fields",
)


class DuplicateGuardSettings(Document):
    """Validates configuration and keeps derived caches fresh.

    A *Single* DocType stores exactly one record of settings for the whole
    site. This controller runs whenever an administrator saves those settings.
    """

    def validate(self):
        self._validate_phone_config()
        self._validate_employee_config()
        self._validate_field_maps()
        self._normalise_text_lists()

    # -- phone -------------------------------------------------------------
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

    # -- employee ----------------------------------------------------------
    def _validate_employee_config(self):
        """Catch mistakes in the Employee rules that would silently misfire."""
        statuses = _tokens(self.employee_active_statuses)
        unknown = [s for s in statuses if s not in _KNOWN_EMPLOYEE_STATUSES]
        if unknown:
            # A typo here is dangerous rather than merely wrong: no employee
            # would ever match the misspelt status, so every employee would look
            # inactive and ALL employee duplicate checking would quietly stop.
            frappe.throw(
                _("Unknown Employee status(es): {0}. Valid options are: {1}.").format(
                    ", ".join(unknown), ", ".join(sorted(_KNOWN_EMPLOYEE_STATUSES))
                )
            )

        for domain in _tokens(self.employee_email_exempt_domains):
            cleaned = domain.lstrip("@")
            if "@" in cleaned or "." not in cleaned or " " in cleaned:
                frappe.throw(
                    _(
                        "'{0}' is not a valid email domain. Enter the domain only, "
                        "e.g. 'example.com' - not a full address."
                    ).format(domain)
                )

    # -- field maps --------------------------------------------------------
    def _validate_field_maps(self):
        """Ensure the 'DocType: field, field' settings are actually parseable.

        A line the parser cannot read is skipped silently at runtime, so the
        administrator would believe an override was in force when it never was.
        """
        for fieldname in _FIELD_MAP_SETTINGS:
            raw = (self.get(fieldname) or "").strip()
            if not raw:
                continue
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                if ":" not in line:
                    frappe.throw(
                        _(
                            "{0}: the line '{1}' is not in the expected "
                            "'DocType: fieldname, fieldname' format."
                        ).format(_(self.meta.get_label(fieldname)), line)
                    )
                doctype = line.split(":", 1)[0].strip()
                if not frappe.db.exists("DocType", doctype):
                    frappe.throw(
                        _("{0}: '{1}' is not an existing DocType.").format(
                            _(self.meta.get_label(fieldname)), doctype
                        )
                    )

    # -- tidy up -----------------------------------------------------------
    def _normalise_text_lists(self):
        """Trim blank lines and stray whitespace from the multi-line settings."""
        for fieldname in ("guarded_doctypes", "employee_active_statuses") + _FIELD_MAP_SETTINGS:
            value = self.get(fieldname)
            if value:
                lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
                self.set(fieldname, "\n".join(lines))

        if self.employee_email_exempt_domains:
            domains = [d.lstrip("@").lower() for d in _tokens(self.employee_email_exempt_domains)]
            self.employee_email_exempt_domains = "\n".join(domains)

    def on_update(self):
        """Clear engine caches so new settings take effect immediately."""
        # The field-map cache depends on the guarded-doctypes list and the
        # field overrides, so clear it whenever settings change.
        from duplicate_guard.core.metadata import clear_field_cache

        clear_field_cache()
        # Single DocTypes are cached by Frappe; drop that cache too.
        frappe.clear_document_cache("Duplicate Guard Settings", "Duplicate Guard Settings")


def _tokens(value):
    """Split a comma / newline separated setting into clean tokens."""
    if not value:
        return []
    return [t.strip() for t in value.replace(",", "\n").splitlines() if t.strip()]

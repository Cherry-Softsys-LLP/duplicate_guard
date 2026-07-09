"""
duplicate_guard.setup.install
=================================

One-time setup that runs automatically after the app is installed or migrated.

Wired in ``hooks.py``::

    after_install = "duplicate_guard.setup.install.after_install"
    after_migrate = "duplicate_guard.setup.install.after_migrate"

What it does
------------
* Creates the ``legacy_yetiforce_id`` custom field on Customer and Lead
  (indexed, for fast legacy look-ups).
* Ensures the *Duplicate Guard Settings* single document exists with sane
  defaults (guard enabled, Strict Mode on, India phone defaults).

All operations are **idempotent** - safe to run any number of times.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

LEGACY_FIELD = "legacy_yetiforce_id"


def _legacy_field_definition(insert_after):
    """Return the custom-field definition dict for the legacy id field."""
    return {
        "fieldname": LEGACY_FIELD,
        "label": "Legacy YetiForce ID",
        "fieldtype": "Data",
        "insert_after": insert_after,
        # Indexed so ``upsert_by_legacy_id`` look-ups are O(log n), not a scan.
        "search_index": 1,
        # Unique would be ideal, but on a messy legacy site it can block the
        # import itself; we enforce single-copy behaviour in code (the upsert
        # helper + the before_insert guard) which is friendlier during
        # migration. Admins who want a hard DB constraint can flip this to 1.
        "unique": 0,
        "read_only": 0,
        "no_copy": 1,
        "translatable": 0,
        "description": "External identifier carried over from the legacy YetiForce CRM.",
    }


def create_custom_fields_for_legacy():
    """Create ``legacy_yetiforce_id`` on Customer and Lead if missing."""
    custom_fields = {
        "Customer": [_legacy_field_definition(insert_after="customer_name")],
        "Lead": [_legacy_field_definition(insert_after="company_name")],
    }
    # ``update=True`` makes this idempotent: existing fields are updated in
    # place rather than raising a duplicate error.
    create_custom_fields(custom_fields, update=True)


def ensure_settings():
    """Create the settings single doc with defaults if it does not exist."""
    if frappe.db.exists("Duplicate Guard Settings", "Duplicate Guard Settings"):
        return

    settings = frappe.new_doc("Duplicate Guard Settings")
    settings.enabled = 1
    settings.strict_mode = 1
    settings.migration_mode = 0
    settings.check_names = 1
    settings.check_phones = 1
    settings.check_emails = 1
    settings.default_country_code = "91"
    settings.default_region = "IN"
    settings.national_number_length = 10
    settings.guarded_doctypes = "Customer\nLead\nSupplier\nEmployee\nContact"
    settings.function_scopes = "Sales: Lead, Customer\nPurchase: Supplier\nHR: Employee"
    settings.insert(ignore_permissions=True)


def after_install():
    """Entry point for the ``after_install`` hook."""
    create_custom_fields_for_legacy()
    ensure_settings()
    frappe.db.commit()


def after_migrate():
    """Entry point for the ``after_migrate`` hook.

    Migrations can add new standard fields or reset customizations, so we
    re-assert our custom fields and settings on every migrate. Idempotent.
    """
    create_custom_fields_for_legacy()
    ensure_settings()
    frappe.db.commit()

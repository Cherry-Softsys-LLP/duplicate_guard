"""
duplicate_guard.hooks
=========================

Frappe reads this file to discover *everything* the app wants to plug into. It
is the single source of truth that connects our Python functions to Frappe's
lifecycle. Nothing in this app does anything until it is referenced here.

The most important section is ``doc_events``: it maps each guarded DocType to
our handler functions. Because these are DocType *document events*, they fire no
matter how a record is created or changed - desk UI, REST API, Data Import,
background jobs, ``bench execute``, Server Scripts, or a raw ``doc.insert()``.
"""

# ---------------------------------------------------------------------------
# App metadata
# ---------------------------------------------------------------------------
app_name = "duplicate_guard"
app_title = "Duplicate Guard"
app_publisher = "Your Company"
app_description = "Prevent duplicate Leads and Customers in ERPNext (v15 & v16)."
app_email = "support@example.com"
app_license = "MIT"
app_version = "1.0.0"

# Minimum Frappe/ERPNext line this app is built and tested against.
required_apps = ["frappe"]

# ---------------------------------------------------------------------------
# Document events
# ---------------------------------------------------------------------------
# For each DocType we hook:
#   validate      -> run the duplicate check (may reject the save)
#   before_insert -> legacy-id safety net
#   after_insert  -> add the record's normalized values to the index
#   on_update     -> refresh the record's index rows
#   on_trash      -> remove the record's index rows
#
# To guard an additional DocType later, add a block here mirroring these two,
# add the DocType to the Settings 'guarded_doctypes' list, then run
# `bench execute duplicate_guard.api.rebuild_index` for it.
doc_events = {
    "Customer": {
        "before_insert": "duplicate_guard.handlers.customer.before_insert_customer",
        "validate": "duplicate_guard.handlers.customer.validate_customer",
        "after_insert": "duplicate_guard.core.index.sync_document",
        "on_update": "duplicate_guard.core.index.sync_document",
        "on_trash": "duplicate_guard.core.index.delete_index_on_trash",
    },
    "Lead": {
        "before_insert": "duplicate_guard.handlers.lead.before_insert_lead",
        "validate": "duplicate_guard.handlers.lead.validate_lead",
        "after_insert": "duplicate_guard.core.index.sync_document",
        "on_update": "duplicate_guard.core.index.sync_document",
        "on_trash": "duplicate_guard.core.index.delete_index_on_trash",
    },
    # Contact holds the phone/email for Customers (and any party). Guarding it is
    # what makes phone/email duplicate detection work for Customers, whose own
    # phone/email fields are only read-only copies of the primary Contact.
    "Contact": {
        "validate": "duplicate_guard.handlers.contact.validate_contact",
        "after_insert": "duplicate_guard.core.index.sync_document",
        "on_update": "duplicate_guard.core.index.sync_document",
        "on_trash": "duplicate_guard.core.index.delete_index_on_trash",
    },
    # Supplier = the Purchase function. Name checked here; phone/email come via
    # its linked Contacts (which take the Purchase scope).
    "Supplier": {
        "validate": "duplicate_guard.handlers.supplier.validate_supplier",
        "after_insert": "duplicate_guard.core.index.sync_document",
        "on_update": "duplicate_guard.core.index.sync_document",
        "on_trash": "duplicate_guard.core.index.delete_index_on_trash",
    },
    # Employee = the HR function. Contact details live directly on the Employee.
    "Employee": {
        "validate": "duplicate_guard.handlers.employee.validate_employee",
        "after_insert": "duplicate_guard.core.index.sync_document",
        "on_update": "duplicate_guard.core.index.sync_document",
        "on_trash": "duplicate_guard.core.index.delete_index_on_trash",
    },
    # When a DocType's schema changes (or a Custom Field is added/removed), drop
    # the cached phone/email/name field maps so discovery re-runs immediately.
    "DocType": {
        "on_update": "duplicate_guard.core.metadata.clear_field_cache",
    },
    "Custom Field": {
        "on_update": "duplicate_guard.core.metadata.clear_field_cache",
        "after_delete": "duplicate_guard.core.metadata.clear_field_cache",
    },
}

# ---------------------------------------------------------------------------
# Install / migrate hooks
# ---------------------------------------------------------------------------
after_install = "duplicate_guard.setup.install.after_install"
after_migrate = "duplicate_guard.setup.install.after_migrate"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
# Export the legacy custom fields so they travel with the app between sites.
fixtures = [
    {
        "doctype": "Custom Field",
        "filters": {
            "name": [
                "in",
                [
                    "Customer-legacy_yetiforce_id",
                    "Lead-legacy_yetiforce_id",
                ],
            ]
        },
    }
]

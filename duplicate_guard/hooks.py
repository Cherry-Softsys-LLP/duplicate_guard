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
app_publisher = "Your Company"          # TODO: set before publishing
app_description = (
    "Prevent duplicate customers, leads, suppliers, employees and contacts in "
    "ERPNext, scoped per business function."
)
app_email = "support@example.com"       # TODO: set before publishing
app_license = "MIT"
app_version = "1.1.0"

# ERPNext is a hard requirement, not an optional extra: every DocType this app
# guards out of the box (Customer, Lead, Supplier, Employee) is defined by
# ERPNext. Declaring only "frappe" would let the app install onto a bare Frappe
# site, where it would then fail the moment anything touched a guarded DocType.
required_apps = ["frappe", "erpnext"]

# ---------------------------------------------------------------------------
# Document events
# ---------------------------------------------------------------------------
# For each DocType we hook:
#   validate      -> run the duplicate check (may reject the save)
#   before_insert -> legacy-id safety net (opt-in field only)
#   after_insert  -> add the record's normalized values to the index
#   on_update     -> refresh the record's index rows
#   after_rename  -> move the index rows to the record's new name
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
        "after_rename": "duplicate_guard.core.index.sync_on_rename",
        "on_trash": "duplicate_guard.core.index.delete_index_on_trash",
    },
    "Lead": {
        "before_insert": "duplicate_guard.handlers.lead.before_insert_lead",
        "validate": "duplicate_guard.handlers.lead.validate_lead",
        "after_insert": "duplicate_guard.core.index.sync_document",
        "on_update": "duplicate_guard.core.index.sync_document",
        "after_rename": "duplicate_guard.core.index.sync_on_rename",
        "on_trash": "duplicate_guard.core.index.delete_index_on_trash",
    },
    # Contact holds the phone/email for Customers (and any party). Guarding it is
    # what makes phone/email duplicate detection work for Customers, whose own
    # phone/email fields are only read-only copies of the primary Contact.
    "Contact": {
        "validate": "duplicate_guard.handlers.contact.validate_contact",
        "after_insert": "duplicate_guard.core.index.sync_document",
        "on_update": "duplicate_guard.core.index.sync_document",
        "after_rename": "duplicate_guard.core.index.sync_on_rename",
        "on_trash": "duplicate_guard.core.index.delete_index_on_trash",
    },
    # Supplier = the Purchase function. Name checked here; phone/email come via
    # its linked Contacts (which take the Purchase scope).
    "Supplier": {
        "validate": "duplicate_guard.handlers.supplier.validate_supplier",
        "after_insert": "duplicate_guard.core.index.sync_document",
        "on_update": "duplicate_guard.core.index.sync_document",
        "after_rename": "duplicate_guard.core.index.sync_on_rename",
        "on_trash": "duplicate_guard.core.index.delete_index_on_trash",
    },
    # Employee = the HR function. Contact details live directly on the Employee.
    "Employee": {
        "validate": "duplicate_guard.handlers.employee.validate_employee",
        "after_insert": "duplicate_guard.core.index.sync_document",
        "on_update": "duplicate_guard.core.index.sync_document",
        "after_rename": "duplicate_guard.core.index.sync_on_rename",
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
# Deliberately none.
#
# The legacy-id custom field used to be exported here. It must not be: it is an
# opt-in migration aid (see setup/install.py), so exporting it as a fixture would
# push a "Legacy ID" field onto the Customer and Lead forms of every site that
# installs this app - including the great majority that are not migrating from
# anywhere and would have no idea what it was for.

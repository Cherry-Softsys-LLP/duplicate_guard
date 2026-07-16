"""
duplicate_guard.setup.install
=================================

One-time setup that runs automatically after the app is installed or migrated.

Wired in ``hooks.py``::

    after_install = "duplicate_guard.setup.install.after_install"
    after_migrate = "duplicate_guard.setup.install.after_migrate"

What it does
------------
* Ensures the *Duplicate Guard Settings* single document exists, with defaults
  chosen by **looking at the site's data** (see below).
* Creates the ``legacy_id`` custom field **only** on sites that opt in
  (see :func:`create_custom_fields_for_legacy`).
* Re-indexes automatically when the engine's matching logic changes.

Why the defaults depend on existing data
----------------------------------------
Installing a strict duplicate guard onto a database that already contains
duplicates is actively harmful. Two Customers that share a phone number are not
a hypothetical - they are ordinary legacy data. With Strict Mode on, the *first*
person to edit either record (to change a credit limit, an address, anything at
all) has their save rejected because of a phone number they never touched. Both
records become uneditable, background jobs that touch them start failing, and the
error message blames a field the user was not editing. The app has not prevented
a single new duplicate; it has just broken the site.

So: a site that already has party records is set up in **Migration Mode**, where
collisions are recorded to *Duplicate Report* and nothing is ever blocked. The
administrator cleans up, then turns Migration Mode off to go strict. Only an
**empty** site - where there is nothing to break - starts in Strict Mode.

All operations are **idempotent** - safe to run any number of times.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

LEGACY_FIELD = "legacy_id"

# Party DocTypes whose row counts decide whether this site has "existing data".
# Contact is included: a site can carry thousands of Contacts with almost no
# Customers, and those Contacts are exactly what collides on phone/email.
_DATA_DOCTYPES = ["Customer", "Lead", "Supplier", "Employee", "Contact"]

# Bumped whenever a change to the engine alters what gets indexed, so an
# existing site re-indexes itself on ``bench migrate`` instead of silently
# running against an index built by the old rules. History:
#   1 - initial release.
#   2 - Employee name indexing; inactive employees excluded; company-domain
#       employee emails exempted.
#   3 - Wider phone/email field discovery (Read Only fieldtype, token-based
#       name matching, fetch_from mirrors skipped) AND per-record phone region
#       resolution from the country field. Both change which values are stored
#       and how they are normalized, so an index built by v2 is not comparable.
INDEX_VERSION = 3
_INDEX_VERSION_KEY = "duplicate_guard_index_version"

# Set to 1 in site_config.json to create the legacy-id custom field:
#   "duplicate_guard_enable_legacy_id": 1
# It is off by default because it is only useful to sites migrating from another
# system, and would otherwise put an unexplained "Legacy ID" field on the
# Customer and Lead forms of every site that installs this app.
_LEGACY_FLAG = "duplicate_guard_enable_legacy_id"


def _legacy_field_definition(insert_after):
    """Return the custom-field definition dict for the legacy id field."""
    return {
        "fieldname": LEGACY_FIELD,
        "label": "Legacy ID",
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
        "description": "Identifier carried over from the system this record was migrated from. Used by the upsert import API so re-importing updates the existing record instead of duplicating it.",
    }


def legacy_id_enabled():
    """Return ``True`` when this site opted into the legacy-id field."""
    return bool(frappe.conf.get(_LEGACY_FLAG))


def create_custom_fields_for_legacy():
    """Create ``legacy_id`` on Customer and Lead - if opted in.

    The legacy id is a migration aid for organisations importing from another
    system. Creating it unconditionally would add an unexplained field to every
    installation, so it is gated behind a site_config flag (see
    :data:`_LEGACY_FLAG`).
    """
    if not legacy_id_enabled():
        return False

    custom_fields = {
        "Customer": [_legacy_field_definition(insert_after="customer_name")],
        "Lead": [_legacy_field_definition(insert_after="company_name")],
    }
    # ``update=True`` makes this idempotent: existing fields are updated in
    # place rather than raising a duplicate error.
    create_custom_fields(custom_fields, update=True)
    return True


def count_existing_records():
    """Return ``{doctype: count}`` for the party DocTypes this app guards."""
    counts = {}
    for doctype in _DATA_DOCTYPES:
        if not frappe.db.table_exists(doctype):
            continue
        try:
            counts[doctype] = frappe.db.count(doctype)
        except Exception:
            counts[doctype] = 0
    return counts


def site_has_existing_data(counts=None):
    """Return ``True`` when the site already holds party records.

    Such a site may contain duplicates that predate the app, so it must start in
    Migration Mode rather than blocking saves on day one.
    """
    counts = count_existing_records() if counts is None else counts
    return any(count > 0 for count in counts.values())


def ensure_settings():
    """Create the settings single doc, with data-aware defaults, if missing.

    An existing settings document is never overwritten - an administrator's
    choices always win over these defaults.
    """
    if frappe.db.exists("Duplicate Guard Settings", "Duplicate Guard Settings"):
        return None

    counts = count_existing_records()
    has_data = site_has_existing_data(counts)

    settings = frappe.new_doc("Duplicate Guard Settings")
    settings.enabled = 1
    settings.strict_mode = 1
    # The one decision that matters. On a site with data, Migration Mode
    # suspends strict enforcement (see ``utils.is_strict_mode``) so nothing is
    # blocked until the administrator has reviewed the Duplicate Report.
    settings.migration_mode = 1 if has_data else 0
    settings.check_names = 1
    settings.check_phones = 1
    settings.check_emails = 1
    settings.default_country_code = "91"
    settings.default_region = "IN"
    settings.national_number_length = 10
    settings.guarded_doctypes = "Customer\nLead\nSupplier\nEmployee\nContact"
    settings.function_scopes = "Sales: Lead, Customer\nPurchase: Supplier\nHR: Employee"
    settings.insert(ignore_permissions=True)

    return {"has_data": has_data, "counts": counts}


def get_index_version():
    """Return the engine version the current index was built with (0 if never)."""
    try:
        return int(frappe.db.get_default(_INDEX_VERSION_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def set_index_version(version=INDEX_VERSION):
    """Stamp the index with the engine version that built it."""
    frappe.db.set_default(_INDEX_VERSION_KEY, str(version))


def index_is_stale():
    """Return ``True`` when the index was built by an older engine version."""
    return get_index_version() < INDEX_VERSION


def _print(message):
    """Write a line to the bench console during install/migrate."""
    try:
        print(message)
    except Exception:
        pass


def _print_next_steps(counts):
    """Tell the administrator exactly what to do on a site with existing data."""
    total = sum(counts.values())
    _print("")
    _print("=" * 72)
    _print("  Duplicate Guard installed in MIGRATION MODE (nothing is blocked)")
    _print("=" * 72)
    _print("  This site already has {0} records in guarded DocTypes:".format(total))
    for doctype, count in sorted(counts.items()):
        if count:
            _print("      {0:<10} {1}".format(doctype, count))
    _print("")
    _print("  Existing duplicates would make records uneditable if the guard")
    _print("  blocked saves today, so enforcement is suspended until you say so.")
    _print("")
    _print("  Next steps:")
    _print("    1. Build the index and audit your data:")
    _print("         bench --site <site> execute duplicate_guard.api.setup_existing_site")
    _print("    2. Review the results in the 'Duplicate Report' list and clean up.")
    _print("    3. Turn OFF Migration Mode in 'Duplicate Guard Settings' to enforce.")
    _print("=" * 72)
    _print("")


def after_install():
    """Entry point for the ``after_install`` hook.

    Deliberately does **not** build the index: on a large site that would run for
    minutes inside the install transaction and time out. The index is built by
    ``duplicate_guard.api.setup_existing_site``, which the message below tells
    the administrator to run.
    """
    create_custom_fields_for_legacy()
    result = ensure_settings()

    # A brand-new (empty) site has a correct, empty index by definition.
    if result and not result["has_data"]:
        set_index_version()

    frappe.db.commit()

    if result and result["has_data"]:
        _print_next_steps(result["counts"])


def after_migrate():
    """Entry point for the ``after_migrate`` hook.

    Migrations can add new standard fields or reset customizations, so we
    re-assert our custom fields and settings on every migrate. Idempotent.

    Also checks the index version: when an app update changes *what* gets
    indexed, an index built by the previous version is quietly wrong (it can
    both miss real duplicates and block on values that are no longer indexed).
    Rather than rebuild automatically - which could run for a long time on a
    large site during a migrate - we detect it and tell the administrator.
    """
    create_custom_fields_for_legacy()
    ensure_settings()
    frappe.db.commit()

    if index_is_stale():
        _print("")
        _print("-" * 72)
        _print("  Duplicate Guard: the duplicate index is out of date.")
        _print("  This version changed what gets indexed, so the existing index")
        _print("  no longer matches the engine's rules.")
        _print("")
        _print("  Rebuild it with:")
        _print("      bench --site <site> execute duplicate_guard.api.rebuild_all_indexes")
        _print("  then stamp it with:")
        _print("      bench --site <site> execute duplicate_guard.api.mark_index_current")
        _print("-" * 72)
        _print("")

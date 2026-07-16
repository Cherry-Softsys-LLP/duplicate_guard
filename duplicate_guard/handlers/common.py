"""
duplicate_guard.handlers.common
===================================

Shared handler logic used by the Customer and Lead handlers.

Currently this holds the *legacy id* guard. The supported way to import data
from a previous system is the upsert API
(:func:`duplicate_guard.api.upsert_by_legacy_id`), which **updates** an existing
record when its ``legacy_id`` is already present instead of creating a second
copy of the same entity.

This guard is the safety net for the *other* path: if someone plainly ``insert``s
a brand-new record that reuses a legacy id (bypassing the upsert helper), we
block it with a clear, actionable error rather than silently creating a duplicate.

The legacy id field is **opt-in** - see
:func:`duplicate_guard.setup.install.legacy_id_enabled`. On sites that have not
enabled it the field does not exist, no value is ever set, and this guard costs
nothing.
"""

import frappe
from frappe import _

from duplicate_guard.core.exceptions import DuplicateLegacyError

# The fieldname of the opt-in legacy identifier custom field. Must match the
# definition in ``duplicate_guard.setup.install``.
LEGACY_FIELD = "legacy_id"

# Set on a document by the upsert API to tell this guard "I know what I am
# doing, this insert is the controlled path".
SKIP_GUARD_FLAG = "duplicate_guard_skip_legacy_guard"


def guard_legacy_id(doc, method=None):
    """``before_insert`` handler: block re-use of an existing legacy id.

    Only relevant on **insert**. On update the record already owns its legacy
    id, so there is nothing to guard.

    ``method`` is accepted because Frappe passes it to doc-event handlers.
    """
    legacy_id = (doc.get(LEGACY_FIELD) or "").strip()
    if not legacy_id:
        return

    # The upsert API performs its own controlled insert and sets this flag so it
    # does not trip its own guard.
    if getattr(doc, "flags", None) and doc.flags.get(SKIP_GUARD_FLAG):
        return

    existing = frappe.db.get_value(doc.doctype, {LEGACY_FIELD: legacy_id}, "name")
    if existing:
        frappe.throw(
            _(
                "A {0} with Legacy ID \"{1}\" already exists ({2}).<br>"
                "To update it during a migration, use the upsert helper "
                "<b>duplicate_guard.api.upsert_by_legacy_id</b> instead of a "
                "plain insert."
            ).format(doc.doctype, legacy_id, existing),
            title=_("Duplicate Legacy ID"),
            exc=DuplicateLegacyError,
        )

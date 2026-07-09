"""
duplicate_guard.handlers.common
===================================

Shared handler logic used by both the Customer and Lead handlers.

Currently this holds the *legacy id* guard. The supported way to import legacy
YetiForce data is the upsert API
(``duplicate_guard.api.upsert_by_legacy_id``), which updates an existing
record when the ``legacy_yetiforce_id`` is already present instead of creating a
duplicate.

This guard is the safety net for the *other* path: if someone tries to plainly
``insert`` a brand-new record that reuses a legacy id (bypassing the upsert
helper), we block it with a clear, actionable error rather than silently
creating a second copy of the same legacy entity.
"""

import frappe
from frappe import _

from duplicate_guard.core.exceptions import DuplicateLegacyError

LEGACY_FIELD = "legacy_yetiforce_id"


def guard_legacy_id(doc, method=None):
    """``before_insert`` handler: block re-use of an existing legacy id.

    Only relevant on **insert**. On update the record already owns its legacy
    id, so there is nothing to guard.

    ``method`` is accepted because Frappe passes it to doc-event handlers.
    """
    legacy_id = (doc.get(LEGACY_FIELD) or "").strip()
    if not legacy_id:
        return

    # ``ignore_flag`` lets the upsert API perform its own controlled insert
    # without tripping this guard.
    if getattr(doc, "flags", None) and doc.flags.get("crm_dg_skip_legacy_guard"):
        return

    existing = frappe.db.get_value(
        doc.doctype, {LEGACY_FIELD: legacy_id}, "name"
    )
    if existing:
        frappe.throw(
            _(
                "A {0} with Legacy YetiForce ID \"{1}\" already exists ({2}).\n"
                "To update it during migration, use the upsert helper "
                "duplicate_guard.api.upsert_by_legacy_id instead of a plain insert."
            ).format(doc.doctype, legacy_id, existing),
            exc=DuplicateLegacyError,
        )

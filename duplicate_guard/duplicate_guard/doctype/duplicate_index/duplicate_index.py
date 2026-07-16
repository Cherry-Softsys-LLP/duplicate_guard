"""Controller for the internal *Duplicate Index* DocType."""

import frappe
from frappe.model.document import Document

# Names of the indexes this module maintains.
SCOPE_VALUE_INDEX = "dg_scope_type_value"
REFERENCE_INDEX = "dg_reference"

# Index names used by releases before 1.1. Dropped on migrate so an upgraded
# site does not carry two identical indexes, which would cost write speed and
# disk for no benefit.
_SUPERSEDED_INDEXES = ("crm_dg_scope_type_value", "crm_dg_reference")


class DuplicateIndex(Document):
    """A single pre-normalized value belonging to one guarded record.

    Rows are created and destroyed by the engine
    (:mod:`duplicate_guard.core.index`), never by hand, which is why the DocType
    is read-only in the UI.
    """

    pass


def on_doctype_update():
    """Create the composite indexes that make duplicate look-ups fast.

    Frappe calls this module-level function whenever the DocType schema is synced
    during ``bench migrate``.

    The single-column ``search_index`` flags in the JSON give us one index per
    column, but the hot query filters on ``scope``, ``value_type`` and
    ``normalized_value`` *together*, so a composite index over the three is what
    turns a duplicate check into a couple of index seeks instead of a scan. The
    second index covers the other hot path: deleting or replacing every row that
    belongs to one record, which happens on every single save of a guarded
    DocType.

    ``frappe.db.add_index`` is idempotent - it will not create an index twice.
    """
    frappe.db.add_index(
        "Duplicate Index",
        ["scope", "value_type", "normalized_value"],
        index_name=SCOPE_VALUE_INDEX,
    )
    frappe.db.add_index(
        "Duplicate Index",
        ["reference_doctype", "reference_name"],
        index_name=REFERENCE_INDEX,
    )
    _drop_superseded_indexes()


def _drop_superseded_indexes():
    """Remove indexes created under their old names by earlier releases.

    Best-effort and deliberately quiet: an index that cannot be dropped is a
    cosmetic problem, and it must never be allowed to fail a ``bench migrate``.
    """
    for index_name in _SUPERSEDED_INDEXES:
        try:
            existing = frappe.db.sql(
                "SHOW INDEX FROM `tabDuplicate Index` WHERE Key_name = %s",
                index_name,
            )
            if existing:
                frappe.db.sql_ddl(
                    "ALTER TABLE `tabDuplicate Index` DROP INDEX `{0}`".format(index_name)
                )
        except Exception:
            # Non-MariaDB backend, insufficient privileges, or already gone.
            pass

"""Controller for the internal *Duplicate Index* DocType."""

import frappe
from frappe.model.document import Document


class DuplicateIndex(Document):
    """A single pre-normalized value belonging to one guarded record.

    Rows are created and destroyed by the engine
    (:mod:`duplicate_guard.core.index`), never by hand, which is
    why the DocType is read-only in the UI.
    """

    pass


def on_doctype_update():
    """Create the composite index that makes duplicate look-ups fast.

    Frappe calls ``on_doctype_update`` (this module-level function) whenever the
    DocType schema is synced during ``bench migrate``. The single-column
    ``search_index`` flags in the JSON give us individual indexes, but the hot
    query filters on ``(value_type, normalized_value)`` together, so a *composite*
    index on those two columns is what we really want.

    ``frappe.db.add_index`` is idempotent - it will not create the index twice.
    """
    frappe.db.add_index(
        "Duplicate Index",
        ["scope", "value_type", "normalized_value"],
        index_name="crm_dg_scope_type_value",
    )
    frappe.db.add_index(
        "Duplicate Index",
        ["reference_doctype", "reference_name"],
        index_name="crm_dg_reference",
    )

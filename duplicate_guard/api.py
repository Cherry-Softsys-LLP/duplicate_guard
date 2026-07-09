"""
duplicate_guard.api
=======================

The public, callable surface of the app.

Everything here is safe to call from:

* ``bench execute`` on the command line,
* a Server Script or a background job,
* the REST API (functions decorated with ``@frappe.whitelist()``),
* your own migration scripts.

Functions
---------
* :func:`upsert_by_legacy_id` - the supported legacy-import primitive: update an
  existing record when its ``legacy_yetiforce_id`` is already present, otherwise
  create a new one.
* :func:`rebuild_index` - (re)build the duplicate index for a DocType. Run this
  once after installing on a site that already has data.
* :func:`check_duplicates` - a read-only "would this collide?" probe that does
  not save anything. Handy for dry-runs and custom UIs.
"""

import json

import frappe
from frappe import _

from duplicate_guard.core import index as index_engine
from duplicate_guard.core import search
from duplicate_guard.core.utils import get_scopes
from duplicate_guard.handlers.common import LEGACY_FIELD


@frappe.whitelist(methods=["POST"])
def rebuild_index(doctype, batch_size=2000):
    """Rebuild the duplicate index for one DocType, in memory-safe batches.

    Example (command line)::

        bench --site yoursite execute \\
            duplicate_guard.api.rebuild_index \\
            --kwargs "{'doctype': 'Customer'}"

    :param doctype: the DocType to index (e.g. ``"Customer"`` or ``"Lead"``).
    :param batch_size: records processed per batch/commit.
    :returns: dict with the number of processed records.
    """
    # ``batch_size`` may arrive as a string when called over REST / CLI.
    batch_size = int(batch_size)
    processed = index_engine.rebuild(doctype, batch_size=batch_size)
    return {"doctype": doctype, "processed": processed}


@frappe.whitelist(methods=["POST"])
def rebuild_all_indexes(batch_size=2000):
    """Rebuild indexes for every guarded DocType."""
    from duplicate_guard.core.utils import get_guarded_doctypes

    results = {}
    for doctype in get_guarded_doctypes():
        results[doctype] = index_engine.rebuild(doctype, batch_size=int(batch_size))
    return results


@frappe.whitelist()
def check_duplicates(doctype, values):
    """Read-only duplicate probe. Does NOT create or modify anything.

    :param doctype: the DocType the values would belong to.
    :param values: a dict (or JSON string) of ``fieldname -> value`` to test.
    :returns: a list of dicts describing each collision found.

    Example::

        check_duplicates("Lead", {"company_name": "ABC Industries",
                                  "mobile_no": "+91 9876543210"})
    """
    if isinstance(values, str):
        values = json.loads(values)

    # Build a transient (unsaved) document so the engine can read its fields
    # exactly as it would during a real save.
    probe = frappe.new_doc(doctype)
    probe.update(values)

    entries = search.collect_entries(probe)
    scopes = get_scopes(probe)
    matches = search.find_duplicates(entries, scopes=scopes, exclude=None)

    return [
        {
            "value_type": m.value_type,
            "value": m.normalized_value,
            "matched_doctype": m.reference_doctype,
            "matched_name": m.reference_name,
            "matched_field": m.source_field,
        }
        for m in matches
    ]


@frappe.whitelist(methods=["POST"])
def upsert_by_legacy_id(doctype, data):
    """Create or update a record keyed by ``legacy_yetiforce_id``.

    This is the supported way to import legacy YetiForce data. If a record of
    ``doctype`` already carries the same ``legacy_yetiforce_id``, that record is
    **updated** with ``data``; otherwise a new record is created. Either way you
    never end up with two rows for the same legacy entity.

    The normal duplicate validation still runs on the resulting document, but
    the originating legacy record is naturally excluded (an update ignores
    itself), so re-importing the same file repeatedly is safe and idempotent.

    :param doctype: e.g. ``"Customer"`` or ``"Lead"``.
    :param data: dict (or JSON string) of field values. Must include
        ``legacy_yetiforce_id``.
    :returns: dict with the resulting ``name`` and whether it was
        ``created`` or ``updated``.
    """
    if isinstance(data, str):
        data = json.loads(data)

    legacy_id = (data.get(LEGACY_FIELD) or "").strip()
    if not legacy_id:
        frappe.throw(
            _("upsert_by_legacy_id requires a non-empty '{0}' value.").format(LEGACY_FIELD)
        )

    existing_name = frappe.db.get_value(doctype, {LEGACY_FIELD: legacy_id}, "name")

    if existing_name:
        doc = frappe.get_doc(doctype, existing_name)
        doc.update(data)
        doc.save(ignore_permissions=True)
        return {"name": doc.name, "action": "updated"}

    doc = frappe.new_doc(doctype)
    doc.update(data)
    # Tell the before_insert legacy guard that this is the controlled upsert
    # path so it does not (incorrectly) complain about the id.
    doc.flags.crm_dg_skip_legacy_guard = True
    doc.insert(ignore_permissions=True)
    return {"name": doc.name, "action": "created"}


@frappe.whitelist(methods=["POST"])
def bulk_upsert_by_legacy_id(doctype, rows, commit_every=200):
    """Upsert many records by ``legacy_yetiforce_id`` in one call.

    This is the recommended way to import legacy data (see also the ready-made
    CSV runner in ``duplicate_guard.scripts.import_legacy``). It calls
    :func:`upsert_by_legacy_id` for each row, but isolates every row with a
    database **savepoint**: if one row fails (bad data, or a Strict-Mode
    duplicate on some other field), only that row is rolled back and recorded as
    an error - the rest of the batch still imports.

    Tip: turn on **Migration Mode** before a legacy import so duplicates on
    name/phone/email are logged to *Duplicate Report* instead of failing the
    row.

    :param doctype: e.g. ``"Customer"`` or ``"Lead"``.
    :param rows: a list of dicts (or a JSON string of that list). Each dict must
        include ``legacy_yetiforce_id``.
    :param commit_every: commit to the database after this many rows.
    :returns: a summary dict ``{"created", "updated", "failed", "errors"}`` where
        ``errors`` is a list of ``{"row", "legacy_id", "error"}``.
    """
    if isinstance(rows, str):
        rows = json.loads(rows)

    commit_every = int(commit_every) or 200
    created = updated = failed = 0
    errors = []

    for i, data in enumerate(rows):
        savepoint = "crm_dg_row_{0}".format(i)
        frappe.db.savepoint(savepoint)
        try:
            result = upsert_by_legacy_id(doctype, data)
            if result["action"] == "created":
                created += 1
            else:
                updated += 1
        except Exception as exc:
            # Undo just this row, keep everything before it in the batch.
            frappe.db.rollback(save_point=savepoint)
            failed += 1
            errors.append(
                {
                    "row": i,
                    "legacy_id": (data or {}).get(LEGACY_FIELD),
                    "error": str(exc),
                }
            )

        if (i + 1) % commit_every == 0:
            frappe.db.commit()

    frappe.db.commit()
    return {
        "created": created,
        "updated": updated,
        "failed": failed,
        "errors": errors,
    }

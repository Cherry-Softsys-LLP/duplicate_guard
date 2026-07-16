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
* :func:`setup_existing_site` - the one command to run after installing on a site
  that already has data: builds the index, audits it, reports what it found.
* :func:`audit_duplicates` - find duplicates that already exist in the data
  (read-only unless you ask it to write reports).
* :func:`upsert_by_legacy_id` - the supported legacy-import primitive: update an
  existing record when its ``legacy_id`` is already present, otherwise
  create a new one.
* :func:`rebuild_index` - (re)build the duplicate index for a DocType. Run this
  once after installing on a site that already has data.
* :func:`check_duplicates` - a read-only "would this collide?" probe that does
  not save anything. Handy for dry-runs and custom UIs.
"""

import json

import frappe
from frappe import _

from duplicate_guard.core import audit as audit_engine
from duplicate_guard.core import index as index_engine
from duplicate_guard.core import search
from duplicate_guard.core.utils import get_scopes
from duplicate_guard.handlers.common import LEGACY_FIELD, SKIP_GUARD_FLAG


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
    """Rebuild indexes for every guarded DocType.

    Stamps the index with the current engine version on success, so
    ``after_migrate`` stops warning that the index is stale.

    DocTypes named in the settings that do not exist on this site are skipped
    rather than raising. The guarded list is free text an administrator can edit,
    and a site may simply not have a given DocType (a typo, or an app that is not
    installed). Letting one bad line abort the whole rebuild would leave the
    index half-built and the version stamp unwritten - a far worse state than a
    skipped DocType, because a half-built index silently misses duplicates.
    """
    from duplicate_guard.core.utils import get_guarded_doctypes
    from duplicate_guard.setup.install import set_index_version

    results = {}
    skipped = []
    for doctype in get_guarded_doctypes():
        if not frappe.db.table_exists(doctype):
            skipped.append(doctype)
            continue
        results[doctype] = index_engine.rebuild(doctype, batch_size=int(batch_size))

    if skipped:
        frappe.msgprint(
            _("Skipped {0}: not present on this site.").format(", ".join(skipped)),
            title=_("Duplicate Guard"),
            indicator="orange",
        )

    set_index_version()
    frappe.db.commit()
    return results


@frappe.whitelist(methods=["POST"])
def mark_index_current():
    """Stamp the index as built by the current engine version.

    Call this after a manual rebuild so ``bench migrate`` stops reporting the
    index as out of date.
    """
    from duplicate_guard.setup.install import INDEX_VERSION, set_index_version

    set_index_version()
    frappe.db.commit()
    return {"index_version": INDEX_VERSION}


@frappe.whitelist()
def audit_duplicates(limit=None, create_reports=False):
    """Find duplicates that **already exist** in the data.

    Unlike the live guard - which only ever sees a record someone is saving -
    this scans the whole ``Duplicate Index`` and reports every genuine collision
    sitting in the database right now. Run it after installing on an existing
    site, *before* switching Migration Mode off.

    Pairs that represent the same business entity (a Contact and the party it
    belongs to; a Customer and the Lead it was converted from) are excluded, so
    the output is real duplicates only.

    Example::

        bench --site yoursite execute duplicate_guard.api.audit_duplicates
        bench --site yoursite execute duplicate_guard.api.audit_duplicates \\
            --kwargs "{'create_reports': True}"

    :param limit: optional cap on the number of duplicate groups returned.
    :param create_reports: when true, also write a *Duplicate Report* row for
        each collision so they can be reviewed in the desk.
    :returns: dict with a ``summary`` and the ``duplicates`` found.
    """
    if isinstance(create_reports, str):
        create_reports = create_reports.lower() in ("1", "true", "yes")

    duplicates = audit_engine.find_existing_duplicates(limit=limit)
    summary = audit_engine.summarise(duplicates)

    reports_created = 0
    if create_reports:
        reports_created = audit_engine.create_reports(duplicates)

    return {
        "summary": summary,
        "reports_created": reports_created,
        "duplicates": duplicates,
    }


@frappe.whitelist(methods=["POST"])
def setup_existing_site(batch_size=2000):
    """Prepare a site that already has data: index it, audit it, report.

    This is the single command an administrator should run after installing the
    app on a live site::

        bench --site yoursite execute duplicate_guard.api.setup_existing_site

    It does three things, in the only order that is safe:

    1. **Builds the index** for every guarded DocType. Until this runs the index
       is empty, so the guard silently matches nothing - and then starts blocking
       weeks later as records get re-saved one by one.
    2. **Audits** the freshly built index for duplicates that already exist,
       writing them to *Duplicate Report*.
    3. **Prints what it found**, and leaves Migration Mode exactly as it is -
       switching to strict enforcement is a decision for the administrator to
       make once the report is clean.

    :param batch_size: records processed per batch during the index build.
    :returns: dict with the index results and the audit summary.
    """
    indexed = rebuild_all_indexes(batch_size=batch_size)

    duplicates = audit_engine.find_existing_duplicates()
    summary = audit_engine.summarise(duplicates)
    reports_created = audit_engine.create_reports(duplicates)

    from duplicate_guard.core.utils import is_migration_mode

    print("")
    print("=" * 72)
    print("  Duplicate Guard: setup complete")
    print("=" * 72)
    print("  Indexed:")
    for doctype, count in sorted(indexed.items()):
        print("      {0:<10} {1}".format(doctype, count))
    print("")
    if summary["groups"]:
        print("  Found {0} duplicate value(s) across {1} record(s).".format(
            summary["groups"], summary["records"]
        ))
        for value_type, count in sorted(summary["by_type"].items()):
            print("      {0:<8} {1}".format(value_type, count))
        print("")
        print("  {0} Duplicate Report row(s) created - review them in the desk:".format(
            reports_created
        ))
        print("      /app/duplicate-report?status=Open")
    else:
        print("  No pre-existing duplicates found. Your data is clean.")
    print("")
    if is_migration_mode():
        print("  Migration Mode is ON - nothing is being blocked yet.")
        print("  Once the report above is clear, turn Migration Mode OFF in")
        print("  'Duplicate Guard Settings' to start preventing new duplicates.")
    else:
        print("  Migration Mode is OFF - duplicates are being blocked.")
    print("=" * 72)
    print("")

    return {
        "indexed": indexed,
        "summary": summary,
        "reports_created": reports_created,
    }


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
    """Create or update a record keyed by ``legacy_id``.

    This is the supported way to import data from a previous system. If a record of
    ``doctype`` already carries the same ``legacy_id``, that record is
    **updated** with ``data``; otherwise a new record is created. Either way you
    never end up with two rows for the same legacy entity.

    The normal duplicate validation still runs on the resulting document, but
    the originating legacy record is naturally excluded (an update ignores
    itself), so re-importing the same file repeatedly is safe and idempotent.

    Requires the legacy-id field, which is opt-in: set
    ``"duplicate_guard_enable_legacy_id": 1`` in site_config.json and run
    ``bench migrate``.

    :param doctype: e.g. ``"Customer"`` or ``"Lead"``.
    :param data: dict (or JSON string) of field values. Must include
        ``legacy_id``.
    :returns: dict with the resulting ``name`` and whether it was
        ``created`` or ``updated``.
    """
    from duplicate_guard.setup.install import legacy_id_enabled

    if not legacy_id_enabled():
        frappe.throw(
            _(
                "The legacy id field is not enabled on this site. Set "
                "'duplicate_guard_enable_legacy_id': 1 in site_config.json and "
                "run 'bench migrate' to use the legacy import API."
            )
        )

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
    doc.flags[SKIP_GUARD_FLAG] = True
    doc.insert(ignore_permissions=True)
    return {"name": doc.name, "action": "created"}


@frappe.whitelist(methods=["POST"])
def bulk_upsert_by_legacy_id(doctype, rows, commit_every=200):
    """Upsert many records by ``legacy_id`` in one call.

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
        include ``legacy_id``.
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
        savepoint = "dg_row_{0}".format(i)
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

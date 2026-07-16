"""
duplicate_guard.scripts.import_legacy
=========================================

A ready-to-run CSV importer for data migrated from a previous system that
**updates existing records instead of creating duplicates**, keyed on
``legacy_id``.

Why this exists
---------------
ERPNext's built-in Data Import tool is great, but it can only *update* existing
records by matching on the ERPNext record id (``name``) - it cannot match on a
custom field like ``legacy_id``. So re-importing a legacy file through
the built-in tool would try to create second copies (which our before_insert
guard then blocks). This script routes each row through
``duplicate_guard.api.upsert_by_legacy_id`` instead, giving you true
idempotent upserts.

How to use it
-------------
1. Put your CSV on the server (e.g. in the bench's ``sites`` folder). The header
   row must use ERPNext **fieldnames** as columns, and must include a
   ``legacy_id`` column. Example for Lead::

       legacy_id,company_name,first_name,mobile_no,email_id
       LEG-1001,ABC Industries,Ravi,+91 9876543210,ravi@abc.com
       LEG-1002,XYZ Traders,Sunil,+91 9812345678,sunil@xyz.com

2. (Recommended) turn on Migration Mode first, so cross-record duplicates are
   logged rather than failing rows.

   The legacy id field is opt-in: set ``"duplicate_guard_enable_legacy_id": 1``
   in site_config.json and run ``bench migrate`` before importing.

3. Run it::

       bench --site yoursite execute \\
           duplicate_guard.scripts.import_legacy.run \\
           --kwargs "{'doctype': 'Lead', 'file_path': 'sites/yoursite/private/files/leads.csv'}"

The function prints a summary and returns it, so the failed rows (if any) are
easy to see and fix.
"""

import csv

import frappe

from duplicate_guard import api


def _clean_row(row, skip_blanks=True):
    """Return a copy of ``row`` with surrounding whitespace stripped.

    When ``skip_blanks`` is true (the default), empty cells are dropped so an
    import never overwrites existing data with blank values.
    """
    cleaned = {}
    for key, value in row.items():
        if key is None:
            continue
        field = key.strip()
        val = value.strip() if isinstance(value, str) else value
        if skip_blanks and (val is None or val == ""):
            continue
        cleaned[field] = val
    return cleaned


def run(
    doctype,
    file_path,
    batch_size=500,
    commit_every=200,
    skip_blanks=True,
    encoding="utf-8-sig",
):
    """Import a CSV of legacy records via idempotent upsert, streaming in batches.

    The file is read and processed ``batch_size`` rows at a time, so memory stays
    bounded even for very large exports (hundreds of thousands of rows).

    :param doctype: target DocType, e.g. ``"Customer"`` or ``"Lead"``.
    :param file_path: path to the CSV file, relative to the bench folder or
        absolute. The header row must use ERPNext fieldnames and include
        ``legacy_id``.
    :param batch_size: how many rows to hold in memory and upsert per batch.
    :param commit_every: commit to the database after this many rows within a
        batch.
    :param skip_blanks: when true, empty CSV cells are ignored (they will not
        overwrite existing values on update).
    :param encoding: file encoding. ``utf-8-sig`` transparently handles files
        saved by Excel with a byte-order mark.
    :returns: an aggregate summary dict
        ``{"created", "updated", "failed", "errors"}``. Each error carries the
        row's line number within the file (0-based, excluding the header).
    """
    totals = {"created": 0, "updated": 0, "failed": 0, "errors": []}

    def _flush(rows, offset):
        if not rows:
            return
        result = api.bulk_upsert_by_legacy_id(doctype, rows, commit_every=commit_every)
        totals["created"] += result["created"]
        totals["updated"] += result["updated"]
        totals["failed"] += result["failed"]
        for err in result["errors"]:
            err = dict(err)
            # Translate the batch-local row index into a file-global one.
            err["row"] = err["row"] + offset
            totals["errors"].append(err)

    total_rows = 0
    with open(file_path, newline="", encoding=encoding) as handle:
        reader = csv.DictReader(handle)
        batch = []
        offset = 0
        for row in reader:
            batch.append(_clean_row(row, skip_blanks=skip_blanks))
            total_rows += 1
            if len(batch) >= batch_size:
                _flush(batch, offset)
                offset += len(batch)
                batch = []
        _flush(batch, offset)

    if total_rows == 0:
        print("No data rows found in {0}".format(file_path))
        return totals

    print(
        "Legacy import for {0}: {1} created, {2} updated, {3} failed "
        "(of {4} rows).".format(
            doctype,
            totals["created"],
            totals["updated"],
            totals["failed"],
            total_rows,
        )
    )
    for err in totals["errors"][:50]:
        print(
            "  Row {0} (legacy id {1}): {2}".format(
                err["row"], err["legacy_id"], err["error"]
            )
        )
    if len(totals["errors"]) > 50:
        print("  ... and {0} more errors.".format(len(totals["errors"]) - 50))

    return totals

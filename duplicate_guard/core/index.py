"""
duplicate_guard.core.index
=========================================

Maintenance of the ``Duplicate Index`` table.

The search engine (:mod:`search`) is only fast because every guarded record's
normalized values are mirrored into this narrow, indexed table. This module
keeps that mirror correct:

* :func:`sync_document`  - (re)write the index rows for one document.
* :func:`delete_for`     - remove the index rows for one document.
* :func:`rebuild`        - (re)build the index for an entire DocType in batches
                           (used for the initial back-fill of legacy data).

These functions are wired to Frappe document events in ``hooks.py``:

    after_insert -> sync_document
    on_update    -> sync_document
    on_trash     -> delete_for
"""

import frappe
from frappe.utils import now

from duplicate_guard.core import search
from duplicate_guard.core.utils import get_scopes, is_enabled, is_guarded

INDEX_DOCTYPE = "Duplicate Index"


def delete_for(reference_doctype, reference_name):
    """Delete every index row belonging to one source document.

    Uses ``frappe.db.delete`` (a parameterized delete) - it never loads the
    rows into Python, so this is cheap even if a record somehow had many
    entries.
    """
    frappe.db.delete(
        INDEX_DOCTYPE,
        {
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
        },
    )


def sync_document(doc, method=None):
    """Rebuild the index rows for a single guarded document.

    Called from ``after_insert`` and ``on_update``. Strategy is
    delete-then-insert for that one reference, which is simple and always
    correct (no diffing bugs). The number of rows per document is tiny (a few
    names/phones/emails), so this is inexpensive.

    ``method`` is accepted because Frappe passes it to doc-event handlers.
    """
    # Never index rows for a document type we do not guard, and skip entirely
    # when the guard is globally disabled.
    if not is_enabled():
        return
    if not is_guarded(doc.doctype):
        return

    # A Lead that has been converted to a Customer is no longer a live record to
    # match against - its phone/email now belong to the new Customer's Contact.
    # Remove its index rows so it neither blocks nor is blocked by that Contact.
    if doc.doctype == "Lead" and (doc.get("status") or "") == "Converted":
        delete_for(doc.doctype, doc.name)
        return

    delete_for(doc.doctype, doc.name)
    _index_document(doc)


def _index_document(doc):
    """Write index rows for a document: one row per (value, scope).

    A document belongs to one or more function scopes (see
    :func:`duplicate_guard.core.utils.get_scopes`). A shared
    Contact linked to both a Customer and a Supplier, for instance, is indexed
    under both the Sales and Purchase scopes so it dedupes correctly within each.
    """
    scopes = get_scopes(doc)
    entries = search.collect_entries(doc)
    for entry in entries:
        for scope in scopes:
            _insert_row(
                value_type=entry.value_type,
                normalized_value=entry.normalized_value,
                scope=scope,
                reference_doctype=doc.doctype,
                reference_name=doc.name,
                source_field=entry.source_field,
            )


def delete_index_on_trash(doc, method=None):
    """``on_trash`` handler: drop the document's index rows when it is deleted."""
    delete_for(doc.doctype, doc.name)


def _insert_row(value_type, normalized_value, scope, reference_doctype, reference_name, source_field):
    """Insert one index row using a direct, fast ``INSERT``.

    We deliberately bypass the normal ``frappe.get_doc(...).insert()`` document
    lifecycle here: the index is internal bookkeeping, not user data, and going
    through the full document machinery for every phone number would be slow
    during a 500k-row back-fill. We still generate a proper document ``name``
    with ``frappe.generate_hash`` so the primary key is unique.
    """
    frappe.db.sql(
        """
        INSERT INTO `tabDuplicate Index`
            (`name`, `creation`, `modified`, `owner`, `modified_by`,
             `value_type`, `normalized_value`, `scope`,
             `reference_doctype`, `reference_name`, `source_field`)
        VALUES
            (%(name)s, %(now)s, %(now)s, %(user)s, %(user)s,
             %(value_type)s, %(normalized_value)s, %(scope)s,
             %(reference_doctype)s, %(reference_name)s, %(source_field)s)
        """,
        {
            "name": frappe.generate_hash(length=12),
            "now": now(),
            "user": frappe.session.user or "Administrator",
            "value_type": value_type,
            "normalized_value": normalized_value,
            "scope": scope,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "source_field": source_field,
        },
    )


def rebuild(doctype, batch_size=2000, commit_every_batch=True):
    """Rebuild the entire index for ``doctype`` in memory-safe batches.

    This is what you run once, after installing the app on an existing site with
    lots of data, so that historical Customers/Leads are searchable::

        bench --site yoursite execute \
            duplicate_guard.api.rebuild_index --kwargs "{'doctype':'Customer'}"

    * Records are streamed with ``limit_start`` / ``limit_page_length`` - we
      never load the whole table at once.
    * Each batch is committed, so a crash mid-way does not lose completed work.

    :param doctype: the DocType to (re)index, e.g. ``"Customer"``.
    :param batch_size: number of records per batch.
    :param commit_every_batch: commit after each batch (recommended for large
        sets). Set ``False`` inside tests that manage their own transaction.
    :returns: the total number of documents processed.
    """
    # Clear any existing index rows for this DocType first, so a rebuild is
    # idempotent (running it twice gives the same result).
    frappe.db.delete(INDEX_DOCTYPE, {"reference_doctype": doctype})

    processed = 0
    start = 0
    while True:
        names = frappe.get_all(
            doctype,
            fields=["name"],
            limit_start=start,
            limit_page_length=batch_size,
            order_by="creation asc",
        )
        if not names:
            break

        for row in names:
            doc = frappe.get_doc(doctype, row["name"])
            # Keep rebuild consistent with live indexing: converted Leads are
            # not indexed (their values live on the new Customer's Contact).
            if doctype == "Lead" and (doc.get("status") or "") == "Converted":
                processed += 1
                continue
            _index_document(doc)
            processed += 1

        if commit_every_batch:
            frappe.db.commit()

        start += batch_size

    return processed

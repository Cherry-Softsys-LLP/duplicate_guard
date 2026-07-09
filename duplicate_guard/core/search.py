"""
duplicate_guard.core.search
==========================================

The search engine.

Two responsibilities live here:

1. :func:`collect_entries` - turn a document into the set of *normalized values*
   that must be unique (its names, phones and emails).
2. :func:`find_duplicates` - given those values, find every other record that
   already owns one of them, using a **single indexed SQL query** against the
   ``Duplicate Index`` table.

Why an index table?
-------------------
The specification requires this to scale to 500,000+ Customers. We must never
load all Customers or all Leads into memory, and we must never scan those big
tables while normalizing on the fly.

The solution is a dedicated, narrow, *pre-normalized* table
(``tabDuplicate Index``) with columns:

    value_type | normalized_value | reference_doctype | reference_name | source_field

It has a composite index on ``(value_type, normalized_value)``. A duplicate
check is therefore a handful of index look-ups, each O(log n) - constant-ish
regardless of how many millions of rows exist. The index rows are maintained
automatically whenever a guarded record is inserted, updated or deleted (see
``duplicate_guard/index.py``).
"""

from collections import namedtuple

import frappe

from duplicate_guard.core import metadata, normalizer
from duplicate_guard.core.utils import get_check_types, get_phone_config

INDEX_DOCTYPE = "Duplicate Index"

# A single normalized value extracted from a document.
#
#   value_type       : "Name" | "Phone" | "Email"
#   normalized_value : the canonical comparison string
#   source_field     : the fieldname it came from (for a helpful error message)
Entry = namedtuple("Entry", ["value_type", "normalized_value", "source_field"])

# A match found in the index.
Match = namedtuple(
    "Match",
    [
        "value_type",
        "normalized_value",
        "reference_doctype",
        "reference_name",
        "source_field",
    ],
)


def collect_entries(doc):
    """Return the de-duplicated list of :class:`Entry` values for ``doc``.

    * Which value types are collected is decided per DocType by
      :func:`duplicate_guard.core.utils.get_check_types` (e.g. a
      Customer contributes only its name, a Contact only its phones/emails).
    * Names, phones and emails are discovered dynamically from metadata, at both
      the top level and inside child tables (a Contact's ``phone_nos`` /
      ``email_ids`` grids).
    * Every value is normalized with the engine in :mod:`normalizer`.
    * Duplicate values *inside the same record* are collapsed. For example a
      Contact whose Mobile, Phone and a grid row all hold ``9876543210`` yields a
      single Phone entry - that is valid and must never be flagged.

    Empty / blank fields are ignored.

    :param doc: a Frappe document (``frappe.model.document.Document``) or any
        object exposing ``.doctype`` and ``.get(fieldname)``.
    :returns: list of unique :class:`Entry`.
    """
    doctype = doc.doctype
    check_types = get_check_types(doctype)
    country_code, national_len, region = get_phone_config()

    field_map = metadata.get_field_map(doctype)

    # ``seen`` guarantees uniqueness within this record: we key on
    # (value_type, normalized_value) so the same phone in several fields counts
    # once. We keep the FIRST field that produced it for the error message.
    seen = set()
    entries = []

    def _add(value_type, normalized_value, source_field):
        if not normalized_value:
            return
        key = (value_type, normalized_value)
        if key in seen:
            return
        seen.add(key)
        entries.append(Entry(value_type, normalized_value, source_field))

    def _norm_phone(value):
        return normalizer.normalize_phone(
            value,
            default_country_code=country_code,
            national_number_length=national_len,
            default_region=region,
        )

    # --- Names (top level) ---
    if "Name" in check_types:
        for fieldname in field_map["name"]:
            _add("Name", normalizer.normalize_name(doc.get(fieldname)), fieldname)

    # --- Phones (top level) ---
    if "Phone" in check_types:
        for fieldname in field_map["phone"]:
            _add("Phone", _norm_phone(doc.get(fieldname)), fieldname)

    # --- Emails (top level) ---
    if "Email" in check_types:
        for fieldname in field_map["email"]:
            _add("Email", normalizer.normalize_email(doc.get(fieldname)), fieldname)

    # --- Phones / Emails inside child tables (e.g. Contact phone_nos/email_ids) ---
    _collect_child_values(doc, field_map, check_types, _add, _norm_phone)

    return entries


def _collect_child_values(doc, field_map, check_types, _add, _norm_phone):
    """Collect phone/email values from a document's child-table grids."""
    if "Phone" in check_types:
        for table_field, child_field in field_map.get("child_phone", []):
            for row in (doc.get(table_field) or []):
                value = row.get(child_field) if hasattr(row, "get") else getattr(row, child_field, None)
                _add("Phone", _norm_phone(value), "{0}.{1}".format(table_field, child_field))

    if "Email" in check_types:
        for table_field, child_field in field_map.get("child_email", []):
            for row in (doc.get(table_field) or []):
                value = row.get(child_field) if hasattr(row, "get") else getattr(row, child_field, None)
                _add("Email", normalizer.normalize_email(value), "{0}.{1}".format(table_field, child_field))


def find_duplicates(entries, scopes, exclude=None):
    """Find existing index rows that collide with any of ``entries``.

    :param entries: iterable of :class:`Entry` (typically from
        :func:`collect_entries`).
    :param scopes: iterable of function-scope strings the querying document
        belongs to. Only index rows in one of these scopes can match, so the
        same value in a different business function never collides.
    :param exclude: iterable of ``(doctype, name)`` tuples to ignore. Always
        pass the current document here (so editing a record does not clash with
        itself). During Lead conversion also pass the originating Lead.
    :returns: list of :class:`Match`, one per colliding index row.

    Implementation notes
    ---------------------
    * We build ONE SQL statement. Values are grouped by ``value_type`` and each
      group uses an ``IN (...)`` list; combined with the ``scope IN (...)`` filter
      this uses the ``(scope, value_type, normalized_value)`` composite index.
    * Every value is passed as a bound parameter (``%s``) - never string
      formatted into the SQL - so injection is impossible.
    * Cross-field and cross-DocType detection fall out naturally: within a scope,
      the query cares only that the normalized value matches, not which field or
      DocType it came from.
    """
    entries = list(entries)
    scopes = [s for s in (scopes or []) if s]
    if not entries or not scopes:
        return []

    exclude = list(exclude or [])

    # Group normalized values by their type.
    values_by_type = {}
    for entry in entries:
        values_by_type.setdefault(entry.value_type, []).append(entry.normalized_value)

    # Build the "(value_type = %s AND normalized_value IN (%s, %s, ...))" groups.
    where_groups = []
    params = []
    for value_type, values in values_by_type.items():
        placeholders = ", ".join(["%s"] * len(values))
        where_groups.append(
            "(`value_type` = %s AND `normalized_value` IN ({0}))".format(placeholders)
        )
        params.append(value_type)
        params.extend(values)

    where_sql = " OR ".join(where_groups)

    # Scope filter: only rows in one of the querying doc's scopes can match.
    scope_placeholders = ", ".join(["%s"] * len(scopes))
    scope_sql = " AND `scope` IN ({0})".format(scope_placeholders)
    params.extend(scopes)

    # Exclusions: NOT ( (reference_doctype = %s AND reference_name = %s) OR ... )
    exclude_sql = ""
    for dt, name in exclude:
        if not dt or not name:
            continue
        exclude_sql += " AND NOT (`reference_doctype` = %s AND `reference_name` = %s)"
        params.extend([dt, name])

    query = (
        "SELECT `value_type`, `normalized_value`, `reference_doctype`, "
        "`reference_name`, `source_field` "
        "FROM `tabDuplicate Index` "
        "WHERE ({where}){scope}{exclude}".format(
            where=where_sql, scope=scope_sql, exclude=exclude_sql
        )
    )

    rows = frappe.db.sql(query, params, as_dict=True)

    return [
        Match(
            value_type=row["value_type"],
            normalized_value=row["normalized_value"],
            reference_doctype=row["reference_doctype"],
            reference_name=row["reference_name"],
            source_field=row["source_field"],
        )
        for row in rows
    ]

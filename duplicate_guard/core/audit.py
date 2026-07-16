"""
duplicate_guard.core.audit
=========================================

The **audit scanner**: find duplicates that *already exist* in the data.

Why this module exists
----------------------
The rest of the engine is a *gatekeeper*: :mod:`validator` runs inside the
``validate`` document event, so it only ever sees a record someone is actively
saving. That is exactly right for preventing new duplicates, but it is blind to
the duplicates already sitting in a database on the day the app is installed.

Migration Mode has the same blind spot: it writes *Duplicate Report* rows from
``validate``, so a pre-existing duplicate pair stays invisible until somebody
happens to re-save one of the two records. An administrator who installs the
app, sees an empty Duplicate Report and concludes "my data is clean" is being
misled - the collisions are there, they just have not been touched yet.

This module closes that gap. Because every guarded record's normalized values
are already mirrored into the narrow ``Duplicate Index`` table, finding the
pre-existing collisions is a single grouped query over that table - no scanning
of Customer/Lead/Employee, no re-normalizing, no loading records into memory::

    SELECT scope, value_type, normalized_value
    FROM `tabDuplicate Index`
    GROUP BY scope, value_type, normalized_value
    HAVING COUNT(*) > 1

Same-entity pairs are not duplicates
------------------------------------
A raw ``GROUP BY`` over-reports. A Contact legitimately holds the phone number
of the Lead it belongs to; a Customer legitimately shares its name with the Lead
it was converted from. :func:`~duplicate_guard.core.validator._build_exclusions`
already teaches the live path to ignore those pairs, and the audit must apply the
*same* rule or it will bury the administrator in false positives on the records
that are working exactly as designed.

We do that with :func:`_entity_tokens`: each indexed reference is expanded into
the set of business entities it represents (a Contact expands to every party it
links to, plus the originating Lead of any linked Customer; a Customer expands to
its originating Lead). Two references that share a token are the same entity and
are never reported against each other. A group is only reported when it contains
at least two references that are genuinely *different* entities.

Usage::

    bench --site yoursite execute duplicate_guard.api.audit_duplicates
    bench --site yoursite execute duplicate_guard.api.audit_duplicates \\
        --kwargs "{'create_reports': True}"
"""

import frappe
from frappe.utils import now

from duplicate_guard.core.utils import get_employee_active_statuses

INDEX_DOCTYPE = "Duplicate Index"
REPORT_DOCTYPE = "Duplicate Report"


def _entity_tokens(doctype, name, cache):
    """Return the set of business entities that ``(doctype, name)`` represents.

    The token set always contains the reference itself. Beyond that it mirrors
    :func:`duplicate_guard.core.validator._linked_party_exclusions`:

    * a **Contact** expands to every party in its ``links`` child table, plus the
      originating Lead of any linked Customer;
    * a **Customer** expands to its originating Lead (``lead_name``).

    Two references whose token sets intersect are "the same entity wearing two
    hats" and must never be reported as duplicates of each other.

    ``cache`` is a plain dict reused across the whole scan so each record is
    resolved at most once.
    """
    key = (doctype, name)
    if key in cache:
        return cache[key]

    tokens = {key}

    if doctype == "Contact":
        links = frappe.get_all(
            "Dynamic Link",
            filters={"parenttype": "Contact", "parent": name, "parentfield": "links"},
            fields=["link_doctype", "link_name"],
        )
        for link in links:
            if not link.link_doctype or not link.link_name:
                continue
            tokens.add((link.link_doctype, link.link_name))
            if link.link_doctype == "Customer":
                lead = frappe.db.get_value("Customer", link.link_name, "lead_name")
                if lead:
                    tokens.add(("Lead", lead))

    elif doctype == "Customer":
        lead = frappe.db.get_value("Customer", name, "lead_name")
        if lead:
            tokens.add(("Lead", lead))

    cache[key] = tokens
    return tokens


def _is_live_reference(doctype, name, cache):
    """Return ``False`` for references that should not participate in the audit.

    Currently this drops **inactive employees**: their name / phone / personal
    email are deliberately reusable, so reporting them would be noise. Inactive
    employees are not indexed in the first place, but an index built before this
    rule existed (or a status change that never triggered a re-index) can leave a
    stale row behind, so we re-check here.
    """
    if doctype != "Employee":
        return True

    key = ("__status__", name)
    if key not in cache:
        cache[key] = frappe.db.get_value("Employee", name, "status")
    return cache[key] in get_employee_active_statuses()


def _conflicting_references(refs, cache):
    """Return ``refs`` if it contains two genuinely different entities, else ``[]``.

    ``refs`` is the list of index rows that all share one
    ``(scope, value_type, normalized_value)``. We keep the group only when at
    least one pair of references has disjoint entity tokens - i.e. the shared
    value spans two different businesses/people rather than one entity recorded
    in two places.
    """
    live = [r for r in refs if _is_live_reference(r["reference_doctype"], r["reference_name"], cache)]

    # De-duplicate identical references (the same record can contribute the same
    # value from two fields, e.g. mobile_no and a phone_nos grid row).
    unique = {}
    for row in live:
        unique[(row["reference_doctype"], row["reference_name"])] = row
    rows = list(unique.values())

    if len(rows) < 2:
        return []

    token_sets = [
        _entity_tokens(r["reference_doctype"], r["reference_name"], cache) for r in rows
    ]

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if token_sets[i].isdisjoint(token_sets[j]):
                # At least one genuine cross-entity collision -> report the group.
                return rows

    return []


def find_existing_duplicates(limit=None):
    """Scan the index and return every pre-existing, genuine duplicate group.

    This reads **only** the ``Duplicate Index`` table (plus a few cheap look-ups
    to resolve entity identity), so it is safe to run on a large production site.
    Nothing is written and nothing is blocked.

    :param limit: optional cap on the number of duplicate groups returned.
    :returns: list of dicts, each ``{"scope", "value_type", "value",
        "references": [{"doctype", "name", "source_field"}, ...]}``.
    """
    having_sql = (
        "SELECT `scope`, `value_type`, `normalized_value`, COUNT(*) AS `hits` "
        "FROM `tabDuplicate Index` "
        "GROUP BY `scope`, `value_type`, `normalized_value` "
        "HAVING COUNT(*) > 1 "
        "ORDER BY `hits` DESC"
    )
    if limit:
        having_sql += " LIMIT {0}".format(int(limit) * 5)

    groups = frappe.db.sql(having_sql, as_dict=True)

    cache = {}
    results = []

    for group in groups:
        refs = frappe.db.sql(
            "SELECT `reference_doctype`, `reference_name`, `source_field` "
            "FROM `tabDuplicate Index` "
            "WHERE `scope` = %s AND `value_type` = %s AND `normalized_value` = %s",
            (group["scope"], group["value_type"], group["normalized_value"]),
            as_dict=True,
        )

        conflicting = _conflicting_references(refs, cache)
        if not conflicting:
            continue

        results.append(
            {
                "scope": group["scope"],
                "value_type": group["value_type"],
                "value": group["normalized_value"],
                "references": [
                    {
                        "doctype": r["reference_doctype"],
                        "name": r["reference_name"],
                        "source_field": r["source_field"],
                    }
                    for r in conflicting
                ],
            }
        )

        if limit and len(results) >= int(limit):
            break

    return results


def create_reports(duplicates=None):
    """Write a *Duplicate Report* row for each pre-existing duplicate found.

    The first reference in a group is treated as the "existing" record and every
    other reference is reported against it, which matches the shape of the rows
    Migration Mode produces from ``validate``. Rows are de-duplicated, so running
    the audit repeatedly does not pile up copies.

    :param duplicates: optional pre-computed output of
        :func:`find_existing_duplicates`; recomputed when omitted.
    :returns: the number of report rows created.
    """
    if duplicates is None:
        duplicates = find_existing_duplicates()

    created = 0
    for group in duplicates:
        refs = group["references"]
        primary = refs[0]
        for other in refs[1:]:
            filters = {
                "reference_doctype": other["doctype"],
                "reference_name": other["name"],
                "matched_doctype": primary["doctype"],
                "matched_name": primary["name"],
                "duplicate_type": group["value_type"],
                "duplicate_value": group["value"],
            }
            if frappe.db.exists(REPORT_DOCTYPE, filters):
                continue

            report = frappe.new_doc(REPORT_DOCTYPE)
            report.update(filters)
            report.source_field = other["source_field"]
            report.matched_field = primary["source_field"]
            report.status = "Open"
            report.detected_on = now()
            report.insert(ignore_permissions=True)
            created += 1

    frappe.db.commit()
    return created


def summarise(duplicates=None):
    """Return a small dict summarising the audit, for printing after an install.

    :returns: ``{"groups", "records", "by_type": {...}, "by_scope": {...}}``
    """
    if duplicates is None:
        duplicates = find_existing_duplicates()

    by_type = {}
    by_scope = {}
    records = set()

    for group in duplicates:
        by_type[group["value_type"]] = by_type.get(group["value_type"], 0) + 1
        by_scope[group["scope"]] = by_scope.get(group["scope"], 0) + 1
        for ref in group["references"]:
            records.add((ref["doctype"], ref["name"]))

    return {
        "groups": len(duplicates),
        "records": len(records),
        "by_type": by_type,
        "by_scope": by_scope,
    }

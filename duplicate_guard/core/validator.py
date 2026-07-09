"""
duplicate_guard.core.validator
=============================================

The orchestrator.

:func:`validate_document` is the single entry point that the Customer and Lead
handlers call. It ties the whole engine together:

1. bail out early if the guard is disabled or the DocType is not guarded;
2. collect the document's normalized values (:mod:`search`);
3. work out which records to ignore (the document itself, and - during Lead
   conversion - the originating Lead);
4. search the index for collisions;
5. depending on the active mode, either **reject** with a meaningful error
   (Strict Mode) or **record a report row** and allow the save (Migration Mode).

Because this runs inside the document ``validate`` event, it fires for *every*
way a record can be created or changed: the ERP desk UI, the REST API, the Data
Import tool, background jobs, ``bench execute``, Server Scripts and any
programmatic ``doc.insert()`` / ``doc.save()``.
"""

import frappe
from frappe import _
from frappe.utils import now

from duplicate_guard.core import search
from duplicate_guard.core.exceptions import DuplicateError
from duplicate_guard.core.utils import (
    get_scopes,
    is_enabled,
    is_guarded,
    is_migration_mode,
)

REPORT_DOCTYPE = "Duplicate Report"

# Human-readable labels for each value type, used in error messages.
_TYPE_LABEL = {
    "Name": _("Name"),
    "Phone": _("Phone Number"),
    "Email": _("Email"),
}


def validate_document(doc, extra_exclude=None):
    """Run duplicate validation for ``doc``.

    :param doc: the document being saved.
    :param extra_exclude: optional iterable of ``(doctype, name)`` tuples to
        ignore in addition to the document itself (used for Lead conversion).
    :raises DuplicateError: in Strict Mode when a duplicate is found.
    """
    if not is_enabled():
        return
    if not is_guarded(doc.doctype):
        return

    entries = search.collect_entries(doc)
    if not entries:
        return

    scopes = get_scopes(doc)
    exclude = _build_exclusions(doc, extra_exclude)
    matches = search.find_duplicates(entries, scopes=scopes, exclude=exclude)
    if not matches:
        return

    if is_migration_mode():
        _record_migration_reports(doc, matches)
        return

    # Strict Mode (the default): reject with a detailed message built from the
    # first collision. We also attach the full list to the exception for
    # programmatic callers/tests.
    _raise_duplicate_error(doc, matches, entries)


def _build_exclusions(doc, extra_exclude):
    """Return the list of ``(doctype, name)`` pairs to ignore during search."""
    exclude = []

    # 1) Ignore the current document (so editing a record never clashes with
    #    itself). ``doc.name`` is populated for updates and, in v15/16, for
    #    inserts that use autoname before ``validate``.
    if doc.name:
        exclude.append((doc.doctype, doc.name))

    # 2) Lead conversion: when a Lead becomes a Customer, ERPNext stores the
    #    originating Lead's id in ``Customer.lead_name``. Ignore ONLY that Lead;
    #    everything else is still checked.
    lead_ref = doc.get("lead_name")
    if doc.doctype == "Customer" and lead_ref:
        exclude.append(("Lead", lead_ref))

    # 3) Contact: a Contact holds the phone/email of the party (Customer/Lead) it
    #    is linked to. Those same values legitimately live on the linked party
    #    (a Lead's own phone) or arrived there during Lead-to-Customer conversion.
    #    So ignore every party this Contact is linked to, plus the originating
    #    Lead of any linked Customer. This prevents a Contact from being flagged
    #    as a duplicate of its own entity.
    if doc.doctype == "Contact":
        exclude.extend(_linked_party_exclusions(doc))

    if extra_exclude:
        exclude.extend(extra_exclude)

    return exclude


def _linked_party_exclusions(contact_doc):
    """Return ``(doctype, name)`` pairs to ignore for a Contact.

    Includes every party in the Contact's ``links`` child table and, for any
    linked Customer, that Customer's originating Lead (``lead_name``).
    """
    exclusions = []
    for link in (contact_doc.get("links") or []):
        link_doctype = link.get("link_doctype") if hasattr(link, "get") else getattr(link, "link_doctype", None)
        link_name = link.get("link_name") if hasattr(link, "get") else getattr(link, "link_name", None)
        if not link_doctype or not link_name:
            continue
        exclusions.append((link_doctype, link_name))
        if link_doctype == "Customer":
            originating_lead = frappe.db.get_value("Customer", link_name, "lead_name")
            if originating_lead:
                exclusions.append(("Lead", originating_lead))
    return exclusions


def _match_title(match):
    """Return a friendly display name for the matched record.

    Tries the DocType's ``title_field`` first (e.g. ``customer_name`` /
    ``lead_name`` / ``company_name``), then falls back to the record id.
    """
    meta = frappe.get_meta(match.reference_doctype)
    title_field = meta.get("title_field")
    title = None
    if title_field:
        title = frappe.db.get_value(
            match.reference_doctype, match.reference_name, title_field
        )
    return title or match.reference_name


def _raise_duplicate_error(doc, matches, entries):
    """Build a meaningful message and raise :class:`DuplicateError`.

    Example message::

        Duplicate Phone Number "9876543210" already exists.
        Existing: Customer CUST-00045 (ABC Industries - Sitabuldi)
        Conflicting field on this Lead: whatsapp
    """
    # Map (value_type, normalized_value) -> the field on the CURRENT doc that
    # produced it, so we can tell the user exactly which of their fields clashed.
    source_by_value = {
        (e.value_type, e.normalized_value): e.source_field for e in entries
    }

    first = matches[0]
    type_label = _TYPE_LABEL.get(first.value_type, first.value_type)
    existing_title = _match_title(first)
    current_field = source_by_value.get(
        (first.value_type, first.normalized_value), first.source_field
    )

    lines = [
        _("Duplicate {0} \"{1}\" already exists.").format(
            type_label, first.normalized_value
        ),
        _("Existing: {0} {1} ({2})").format(
            first.reference_doctype, first.reference_name, existing_title
        ),
        _("Conflicting field on this {0}: {1}").format(doc.doctype, current_field),
    ]

    # If several distinct values clashed, summarise the rest so nothing is
    # hidden from the user.
    if len(matches) > 1:
        others = _summarise_extra_matches(matches[1:])
        if others:
            lines.append(_("Other conflicts:"))
            lines.extend(others)

    message = "\n".join(lines)

    error = DuplicateError(message)
    # Attach structured details for programmatic callers and tests.
    error.duplicate_matches = matches
    raise error


def _summarise_extra_matches(matches):
    """Return de-duplicated one-line summaries for additional matches."""
    seen = set()
    lines = []
    for m in matches:
        key = (m.value_type, m.normalized_value, m.reference_doctype, m.reference_name)
        if key in seen:
            continue
        seen.add(key)
        type_label = _TYPE_LABEL.get(m.value_type, m.value_type)
        lines.append(
            _("- {0} \"{1}\" on {2} {3}").format(
                type_label, m.normalized_value, m.reference_doctype, m.reference_name
            )
        )
    return lines


def _record_migration_reports(doc, matches):
    """Create *Duplicate Report* rows for each collision (Migration Mode).

    In Migration Mode we never block the save; instead every collision becomes a
    report row an administrator can review later. Rows are de-duplicated so
    re-saving the same record does not pile up identical reports.
    """
    for match in matches:
        filters = {
            "reference_doctype": doc.doctype,
            "reference_name": doc.name or "",
            "matched_doctype": match.reference_doctype,
            "matched_name": match.reference_name,
            "duplicate_type": match.value_type,
            "duplicate_value": match.normalized_value,
        }
        if frappe.db.exists(REPORT_DOCTYPE, filters):
            continue

        report = frappe.new_doc(REPORT_DOCTYPE)
        report.update(filters)
        report.source_field = _current_source_field(doc, match)
        report.matched_field = match.source_field
        report.status = "Open"
        report.detected_on = now()
        # ``ignore_permissions`` so that even a low-privileged import user can
        # generate the audit trail. This DocType only stores metadata about the
        # collision, not sensitive business data.
        report.insert(ignore_permissions=True)


def _current_source_field(doc, match):
    """Best-effort lookup of the field on ``doc`` that matches ``match``."""
    for entry in search.collect_entries(doc):
        if (
            entry.value_type == match.value_type
            and entry.normalized_value == match.normalized_value
        ):
            return entry.source_field
    return None

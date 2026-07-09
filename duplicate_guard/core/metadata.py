"""
duplicate_guard.core.metadata
============================================

The metadata engine.

This module answers one question for any DocType: *"which fields hold phone
numbers, which hold emails, and which hold the entity name?"*

It never hard-codes field names such as ``mobile_no``, ``phone``, ``fax`` or
``whatsapp``. Instead it **inspects the DocType's metadata** (its schema) at
runtime. That means any custom phone/email field an administrator adds later is
picked up automatically, with zero code changes.

How discovery works
-------------------
Frappe describes every field with a ``fieldtype`` and (optionally) an
``options`` value. Phone and email fields are identified like this:

* **Phone**: ``fieldtype == "Phone"`` **or** a ``Data`` field whose
  ``options`` is ``"Phone"``.
* **Email**: a ``Data`` field whose ``options`` is ``"Email"``.

These are the exact markers ERPNext itself uses for the standard ``mobile_no``,
``phone`` and ``email_id`` fields, so standard fields *and* future custom fields
are both covered by the same rule.

Results are cached per-DocType via :func:`frappe.cache` so we do not walk the
metadata on every single save.
"""

import frappe

from duplicate_guard.core.utils import DEFAULT_NAME_FIELDS

# fieldtypes / options that mark a phone field.
_PHONE_FIELDTYPES = {"Phone"}
_PHONE_OPTIONS = {"Phone"}
# options that mark an email field (always a ``Data`` fieldtype in Frappe).
_EMAIL_OPTIONS = {"Email"}
# fieldtypes that hold a child table (grid) on a parent DocType.
_TABLE_FIELDTYPES = {"Table", "Table MultiSelect"}

# Field-name substrings used as a *fallback* to recognise phone / email fields
# that do not carry an explicit "Phone"/"Email" options marker. This matters for
# child-table fields such as the ``phone`` field of "Contact Phone", which is a
# plain ``Data`` field. Detection still prefers fieldtype/options; these hints
# only apply to ``Data`` fields that were not already classified, so they never
# override an explicit marker. Matching is case-insensitive substring.
_EMAIL_NAME_HINTS = ("email", "e_mail")
_PHONE_NAME_HINTS = (
    "phone", "mobile", "mobile_no", "phone_no", "contact_no",
    "fax", "whatsapp", "telephone", "tel_no", "cell",
)

# Cache key prefix. We bump the version suffix if the discovery logic changes so
# stale caches from an older deployment are ignored. (Bumped to v2 for the
# addition of child-table phone/email discovery.)
_CACHE_PREFIX = "duplicate_guard:fields:v2:"


def _iter_fields(doctype):
    """Yield the field-definition (``docfield``) objects of ``doctype``."""
    meta = frappe.get_meta(doctype)
    for docfield in meta.fields:
        yield docfield


def _classify_field(df):
    """Return ``"phone"``, ``"email"`` or ``None`` for a single field.

    Detection order (first match wins):

    1. fieldtype ``Phone`` -> phone.
    2. ``Data`` field with ``options = "Phone"`` -> phone.
    3. ``Data`` field with ``options = "Email"`` -> email.
    4. Fallback for ``Data`` fields with no such marker: the field name contains
       a well-known email or phone hint (this is what lets us pick up child-table
       fields like "Contact Phone".``phone`` which are plain ``Data`` fields).

    Only ``Data``/``Phone`` fields are ever classified, so ``Check`` fields such
    as ``is_primary_phone`` are correctly ignored.
    """
    fieldtype = df.fieldtype
    options = (df.options or "").strip()

    if fieldtype in _PHONE_FIELDTYPES:
        return "phone"
    if fieldtype != "Data":
        return None
    if options in _PHONE_OPTIONS:
        return "phone"
    if options in _EMAIL_OPTIONS:
        return "email"

    # Fallback: recognise by field name. Email is checked first because email
    # hints and phone hints never overlap.
    fname = (df.fieldname or "").lower()
    if any(hint in fname for hint in _EMAIL_NAME_HINTS):
        return "email"
    if any(hint in fname for hint in _PHONE_NAME_HINTS):
        return "phone"
    return None


def _discover(doctype):
    """Walk the metadata once and return the phone/email/name field lists.

    Handles both **top-level** phone/email fields and phone/email fields that
    live inside **child tables** (e.g. a Contact's ``phone_nos`` / ``email_ids``
    grids). Child-table matches are returned as ``(table_fieldname,
    child_fieldname)`` pairs.

    :returns: ``dict`` with keys:
        * ``phone`` / ``email`` - lists of top-level fieldnames,
        * ``child_phone`` / ``child_email`` - lists of
          ``(table_fieldname, child_fieldname)`` tuples,
        * ``name`` - list of name fieldnames.
    """
    phone_fields = []
    email_fields = []
    child_phone_fields = []
    child_email_fields = []

    for df in _iter_fields(doctype):
        kind = _classify_field(df)
        if kind == "phone":
            phone_fields.append(df.fieldname)
            continue
        if kind == "email":
            email_fields.append(df.fieldname)
            continue

        # Descend one level into child tables to find phone/email fields there.
        if df.fieldtype in _TABLE_FIELDTYPES and df.options:
            try:
                child_meta = frappe.get_meta(df.options)
            except Exception:
                continue
            for child_df in child_meta.fields:
                child_kind = _classify_field(child_df)
                if child_kind == "phone":
                    child_phone_fields.append((df.fieldname, child_df.fieldname))
                elif child_kind == "email":
                    child_email_fields.append((df.fieldname, child_df.fieldname))

    return {
        "phone": phone_fields,
        "email": email_fields,
        "child_phone": child_phone_fields,
        "child_email": child_email_fields,
        "name": _resolve_name_fields(doctype),
    }


def _resolve_name_fields(doctype):
    """Return the name-bearing fields for ``doctype`` that really exist.

    The candidate list comes from *Duplicate Guard Settings* if an override
    is configured for this DocType, otherwise from
    :data:`DEFAULT_NAME_FIELDS`. Candidates that are not actual fields of the
    DocType are dropped, so a typo or a version difference never causes an
    error.
    """
    candidates = _configured_name_fields(doctype) or DEFAULT_NAME_FIELDS.get(doctype, [])

    meta = frappe.get_meta(doctype)
    existing = {df.fieldname for df in meta.fields}
    # ``name`` (the record id / primary key) is always valid even though it is
    # not listed in ``meta.fields``.
    existing.add("name")

    resolved = [f for f in candidates if f in existing]
    return resolved


def _configured_name_fields(doctype):
    """Read a per-DocType name-field override from settings, if any.

    The setting ``name_field_overrides`` is free text of the form::

        Customer: customer_name
        Lead: company_name, lead_name

    Returns a list of fieldnames for ``doctype`` or ``None`` when no override is
    configured for it.
    """
    from duplicate_guard.core.utils import get_settings

    raw = (get_settings().get("name_field_overrides") or "").strip()
    if not raw:
        return None

    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        dt, fields = line.split(":", 1)
        if dt.strip() == doctype:
            names = [f.strip() for f in fields.split(",") if f.strip()]
            return names or None
    return None


def get_field_map(doctype):
    """Return ``{"phone": [...], "email": [...], "name": [...]}`` for ``doctype``.

    The result is cached in Redis (via ``frappe.cache``) keyed by DocType. The
    cache is cleared automatically whenever a DocType or Custom Field is saved
    (see :func:`clear_field_cache`, wired up in ``hooks.py``), so newly added
    custom phone/email fields become active immediately.
    """
    cache = frappe.cache()
    cache_key = _CACHE_PREFIX + doctype
    cached = cache.get_value(cache_key)
    if cached is not None:
        return cached

    field_map = _discover(doctype)
    # Cache for one hour as a safety net; explicit invalidation handles the
    # common case of an admin adding a field.
    cache.set_value(cache_key, field_map, expires_in_sec=3600)
    return field_map


def get_phone_fields(doctype):
    """Convenience accessor: the list of phone fieldnames for ``doctype``."""
    return get_field_map(doctype)["phone"]


def get_email_fields(doctype):
    """Convenience accessor: the list of email fieldnames for ``doctype``."""
    return get_field_map(doctype)["email"]


def get_name_fields(doctype):
    """Convenience accessor: the list of name fieldnames for ``doctype``."""
    return get_field_map(doctype)["name"]


def clear_field_cache(doc=None, method=None):
    """Clear the cached field maps for every guarded DocType.

    Wired to the ``on_update`` events of ``DocType`` and ``Custom Field`` in
    ``hooks.py`` so that adding, changing or removing a phone/email field takes
    effect without a restart. The ``doc``/``method`` parameters are accepted
    because Frappe passes them when calling a doc-event handler; they are not
    used here (we simply clear everything).
    """
    from duplicate_guard.core.utils import get_guarded_doctypes

    cache = frappe.cache()
    for dt in get_guarded_doctypes():
        cache.delete_value(_CACHE_PREFIX + dt)

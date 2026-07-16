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
Detection runs in three tiers, strongest first:

1. **Explicit fieldtype.** ``fieldtype == "Phone"`` is unambiguous.
2. **Explicit options marker.** Frappe marks contact fields with
   ``options = "Phone"`` / ``options = "Email"`` on a ``Data`` field. This is
   what ERPNext's own ``Lead.mobile_no`` and ``Lead.email_id`` use.
3. **Field-name tokens.** Real-world DocTypes - and especially *custom* fields
   added by administrators - frequently carry no marker at all: a phone field is
   often a plain ``Data`` field named ``contact_no`` with empty ``options``.
   ERPNext also uses the ``Read Only`` fieldtype for fetched mirrors such as
   ``Customer.mobile_no``. So as a last resort we look at the field *name*.

Why name matching is done on tokens, not substrings
---------------------------------------------------
A naive ``"email" in fieldname`` check also matches ``email_signature``,
``email_template`` and ``notification_email_footer``; ``"mobile" in fieldname``
matches ``mobile_plan``. Indexing those as contact details produces duplicate
"matches" on values that are not contact details at all, which is worse than
missing a field: it blocks saves for reasons nobody can explain.

So the fieldname is split into tokens and matched against a **hint** set, then
rejected if it contains any **denied** token (``signature``, ``template``,
``provider``, ...). Anything the heuristics cannot be trusted with should be
declared explicitly - see *Overrides* below.

Mirrors are skipped
-------------------
A field with ``fetch_from`` set is a read-only copy of a value that lives on
another record (``Customer.mobile_no`` mirrors the primary Contact's number).
Indexing a mirror registers the same real-world value a second time, under a
second reference, which creates collisions between a record and its own source.
Mirrors are therefore never classified.

Overrides
---------
No heuristic is ever going to be right for every site, so *Duplicate Guard
Settings* can declare fields explicitly (free text, one DocType per line)::

    phone_field_overrides:   Lead: mobile_no, custom_alt_number
    email_field_overrides:   Employee: personal_email, company_email
    ignored_fields:          Lead: email_signature

An override **replaces** discovery for that DocType and value type; the ignore
list is subtracted from whatever discovery (or an override) produced. Both are
optional - leave them blank and detection behaves automatically.

Results are cached per-DocType via :func:`frappe.cache` so we do not walk the
metadata on every single save.
"""

import re

import frappe

from duplicate_guard.core.utils import DEFAULT_NAME_FIELDS

# fieldtypes that mark a phone field outright.
_PHONE_FIELDTYPES = {"Phone"}
# options markers (Frappe sets these on ``Data`` fields).
_PHONE_OPTIONS = {"Phone"}
_EMAIL_OPTIONS = {"Email"}
# fieldtypes that hold a child table (grid) on a parent DocType.
_TABLE_FIELDTYPES = {"Table", "Table MultiSelect"}

# Fieldtypes that may hold a phone number or an email address. ``Data`` is the
# usual one; ``Read Only`` is what ERPNext uses for fetched contact mirrors; and
# ``Phone`` is explicit. Free-text types (``Text``, ``Small Text``,
# ``Long Text``) are deliberately excluded - a notes field mentioning an address
# is not a contact field, and treating it as one would poison the index.
_CLASSIFIABLE_FIELDTYPES = {"Data", "Read Only", "Phone"}

# --- name-based detection -------------------------------------------------
# Tokens that identify a field by name when no explicit marker is present.
_EMAIL_NAME_HINTS = {"email", "emails", "mail", "eid"}
_PHONE_NAME_HINTS = {
    "phone", "phones", "mobile", "cell", "fax", "whatsapp",
    "telephone", "tel", "landline", "msisdn",
}
# Tokens that mean "this is a quantity/label/config about contact details, not a
# contact detail". Any of these vetoes a name-based match.
_NAME_DENY_TOKENS = {
    "signature", "template", "templates", "provider", "plan", "domain",
    "verified", "verification", "validated", "status", "count", "group",
    "list", "subject", "body", "message", "alert", "alerts", "notification",
    "notifications", "footer", "header", "label", "format", "settings",
    "setting", "config", "enabled", "disabled", "required", "allowed",
    "blocked", "opt", "unsubscribed", "bounced", "campaign", "type",
}
# "<qualifier>_<number-word>" fields (``contact_no``, ``alternate_number``) are
# phones even though neither token is a phone hint on its own.
_NUMBER_WORDS = {"no", "nos", "number", "numbers"}
_PHONE_QUALIFIERS = {"contact", "alternate", "alt", "office", "home", "work", "secondary"}

# Cache key prefix. We bump the version suffix if the discovery logic changes so
# stale caches from an older deployment are ignored. (v3: wider fieldtype
# support, token-based name matching, fetch_from mirrors skipped, overrides.)
_CACHE_PREFIX = "duplicate_guard:fields:v3:"


def _tokens(fieldname):
    """Split a fieldname into lower-case word tokens."""
    return {t for t in re.split(r"[^a-z0-9]+", (fieldname or "").lower()) if t}


def _name_kind(fieldname):
    """Return ``"phone"``, ``"email"`` or ``None`` based on the field's name."""
    tokens = _tokens(fieldname)
    if not tokens or (tokens & _NAME_DENY_TOKENS):
        return None
    if tokens & _EMAIL_NAME_HINTS:
        return "email"
    if tokens & _PHONE_NAME_HINTS:
        return "phone"
    if (tokens & _NUMBER_WORDS) and (tokens & _PHONE_QUALIFIERS):
        return "phone"
    return None


def _iter_fields(doctype):
    """Yield the field-definition (``docfield``) objects of ``doctype``."""
    meta = frappe.get_meta(doctype)
    for docfield in meta.fields:
        yield docfield


def _classify_field(df):
    """Return ``"phone"``, ``"email"`` or ``None`` for a single field.

    Detection order (first match wins):

    1. fieldtype ``Phone`` -> phone.
    2. ``options = "Phone"`` -> phone; ``options = "Email"`` -> email.
    3. field-name tokens (see :func:`_name_kind`).

    Fields that mirror another record (``fetch_from``) and virtual fields are
    never classified. Only fieldtypes in :data:`_CLASSIFIABLE_FIELDTYPES` are
    considered, so ``Check`` fields such as ``is_primary_phone`` and free-text
    notes fields are correctly ignored.
    """
    fieldtype = df.fieldtype

    # A fetched mirror is a copy of a value indexed elsewhere; indexing it again
    # would collide a record with its own source.
    if getattr(df, "fetch_from", None):
        return None
    if getattr(df, "is_virtual", 0):
        return None

    if fieldtype in _PHONE_FIELDTYPES:
        return "phone"
    if fieldtype not in _CLASSIFIABLE_FIELDTYPES:
        return None

    options = (df.options or "").strip()
    if options in _PHONE_OPTIONS:
        return "phone"
    if options in _EMAIL_OPTIONS:
        return "email"
    # A Data field carrying some *other* options value (a Link target, a select
    # list, "Name", "URL", ...) is not a contact field.
    if options:
        return None

    return _name_kind(df.fieldname)


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

    # Explicit overrides replace discovery for this DocType / value type.
    phone_override = _configured_fields("phone_field_overrides", doctype)
    if phone_override is not None:
        phone_fields = _existing_only(doctype, phone_override)
    email_override = _configured_fields("email_field_overrides", doctype)
    if email_override is not None:
        email_fields = _existing_only(doctype, email_override)

    # The ignore list is subtracted from whatever we ended up with.
    ignored = set(_configured_fields("ignored_fields", doctype) or [])
    if ignored:
        phone_fields = [f for f in phone_fields if f not in ignored]
        email_fields = [f for f in email_fields if f not in ignored]
        child_phone_fields = [p for p in child_phone_fields if p[1] not in ignored]
        child_email_fields = [p for p in child_email_fields if p[1] not in ignored]

    return {
        "phone": phone_fields,
        "email": email_fields,
        "child_phone": child_phone_fields,
        "child_email": child_email_fields,
        "name": _resolve_name_fields(doctype),
    }


def _existing_only(doctype, fieldnames):
    """Drop any fieldname that is not actually a field of ``doctype``."""
    meta = frappe.get_meta(doctype)
    existing = {df.fieldname for df in meta.fields}
    return [f for f in fieldnames if f in existing]


def _resolve_name_fields(doctype):
    """Return the name-bearing fields for ``doctype`` that really exist.

    The candidate list comes from *Duplicate Guard Settings* if an override
    is configured for this DocType, otherwise from
    :data:`DEFAULT_NAME_FIELDS`. Candidates that are not actual fields of the
    DocType are dropped, so a typo or a version difference never causes an
    error.
    """
    candidates = _configured_fields("name_field_overrides", doctype)
    if candidates is None:
        candidates = DEFAULT_NAME_FIELDS.get(doctype, [])

    meta = frappe.get_meta(doctype)
    existing = {df.fieldname for df in meta.fields}
    # ``name`` (the record id / primary key) is always valid even though it is
    # not listed in ``meta.fields``.
    existing.add("name")

    return [f for f in candidates if f in existing]


def _configured_fields(setting_fieldname, doctype):
    """Read a per-DocType field list from a free-text setting.

    The setting is of the form::

        Customer: customer_name
        Lead: company_name, lead_name

    :returns: a list of fieldnames for ``doctype``, or ``None`` when the setting
        is blank or has no line for this DocType (meaning "no override").
    """
    from duplicate_guard.core.utils import get_settings

    raw = (get_settings().get(setting_fieldname) or "").strip()
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
    """Return the discovered field map for ``doctype``.

    ``{"phone": [...], "email": [...], "child_phone": [...],
    "child_email": [...], "name": [...]}``

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


def describe(doctype):
    """Return a printable summary of what will be checked for ``doctype``.

    Handy for verifying detection on a real site::

        bench --site yoursite execute duplicate_guard.api.describe_fields \\
            --kwargs "{'doctype': 'Lead'}"
    """
    field_map = get_field_map(doctype)
    return {
        "doctype": doctype,
        "name_fields": field_map["name"],
        "phone_fields": field_map["phone"],
        "email_fields": field_map["email"],
        "child_phone_fields": ["{0}.{1}".format(t, f) for t, f in field_map["child_phone"]],
        "child_email_fields": ["{0}.{1}".format(t, f) for t, f in field_map["child_email"]],
    }


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

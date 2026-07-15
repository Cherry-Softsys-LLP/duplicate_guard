"""
duplicate_guard.core.utils
=========================================

Small shared helpers used by the rest of the engine.

The most important thing here is :func:`get_settings`, which reads the single
*Duplicate Guard Settings* document. Because that document is read on every
save of a guarded record, we rely on Frappe's built-in caching for Single
DocTypes (``frappe.get_cached_doc``) so we do not hit the database every time.
"""

import frappe

SETTINGS_DOCTYPE = "Duplicate Guard Settings"

# The DocTypes this app guards out of the box. Address is deliberately NOT here:
# a firm legitimately has several addresses sharing the same contact details.
DEFAULT_GUARDED_DOCTYPES = ["Customer", "Lead", "Supplier", "Employee", "Contact"]

# Default fields that hold the "entity name" we enforce uniqueness on, per
# DocType. Only fields that actually exist in metadata are used.
#   Customer -> customer_name        (Sales)
#   Lead     -> company_name         (Sales, organization name only)
#   Supplier -> supplier_name        (Purchase)
#   Employee -> employee_name        (HR, enforced among ACTIVE employees only)
#   Contact  -> (none)               a Contact is a person; phone/email only
DEFAULT_NAME_FIELDS = {
    "Customer": ["customer_name"],
    "Lead": ["company_name"],
    "Supplier": ["supplier_name"],
    "Employee": ["employee_name"],
}

# Which value types each DocType contributes.
#   Customer / Supplier -> Name only (phones/emails live on their Contacts)
#   Lead                -> phone/email live directly on the record
#   Employee            -> name/phone/email live directly on the record; name
#                          and phone are enforced among ACTIVE employees only,
#                          and company-domain emails are exempt (see below)
#   Contact             -> phone/email only (never a person's name)
DEFAULT_CHECK_TYPES = {
    "Customer": {"Name"},
    "Lead": {"Name", "Phone", "Email"},
    "Supplier": {"Name"},
    "Employee": {"Name", "Phone", "Email"},
    "Contact": {"Phone", "Email"},
}

# Business function each party DocType belongs to. Duplicates are only detected
# WITHIN the same function, so the same phone/email may appear across functions
# (e.g. a person who is both a customer and a supplier) without being flagged.
DEFAULT_FUNCTION_BY_DOCTYPE = {
    "Lead": "Sales",
    "Customer": "Sales",
    "Supplier": "Purchase",
    "Employee": "HR",
}

# Scope used for a Contact that is not linked to any known-function party.
CONTACT_FALLBACK_SCOPE = "Contact"

# ---------------------------------------------------------------------------
# Employee-specific rules
# ---------------------------------------------------------------------------
# Email domains that are EXEMPT from duplicate checks *for Employees*. Official
# company addresses are legitimately shared and reassigned (a resigned
# employee's address is handed to a new hire), so they are never indexed and
# never blocked. Every OTHER (personal) employee email must still be unique
# among active employees. Compared case-insensitively against the address's
# domain part. Edit this set if your company uses more official domains.
EMPLOYEE_EMAIL_EXEMPT_DOMAINS = {"splashjetink.com", "splashjet-ink.com"}

# Employee statuses that count as "currently employed". Name / phone / personal
# email uniqueness is enforced ONLY against employees in one of these statuses,
# so a resigned person's details free up for reuse (including by the same person
# rejoining under a new Employee record). ERPNext's Employee.status options are
# "Active", "Inactive", "Suspended" and "Left"; add "Suspended"/"Inactive" here
# if you want those to keep reserving a person's details.
EMPLOYEE_ACTIVE_STATUSES = {"Active"}


def get_settings():
    """Return the *Duplicate Guard Settings* single document (cached).

    If the document does not exist yet (e.g. the app is installed but
    ``after_install`` has not run for some reason), we fall back to a freshly
    built, unsaved instance carrying the schema defaults so that callers never
    crash with a "DoesNotExistError".
    """
    try:
        return frappe.get_cached_doc(SETTINGS_DOCTYPE)
    except frappe.DoesNotExistError:
        return frappe.new_doc(SETTINGS_DOCTYPE)


def is_enabled():
    """Return ``True`` when the guard is globally switched on."""
    return bool(get_settings().enabled)


def is_migration_mode():
    """Return ``True`` when Migration Mode is active.

    Migration Mode takes precedence over Strict Mode: instead of rejecting a
    duplicate, the engine records a *Duplicate Report* row and lets the save
    proceed. This lets you import messy legacy data first and clean it up later.
    """
    return bool(get_settings().migration_mode)


def is_strict_mode():
    """Return ``True`` when Strict Mode is active and Migration Mode is not.

    Strict Mode is the normal, production behaviour: a detected duplicate is
    rejected with a meaningful error.
    """
    settings = get_settings()
    if settings.migration_mode:
        # Migration mode wins; strict enforcement is suspended.
        return False
    return bool(settings.strict_mode)


def get_guarded_doctypes():
    """Return the list of DocTypes that should be duplicate-checked.

    Reads the ``guarded_doctypes`` field from settings (one DocType name per
    line). Falls back to :data:`DEFAULT_GUARDED_DOCTYPES` when empty.
    """
    settings = get_settings()
    raw = (settings.get("guarded_doctypes") or "").strip()
    if not raw:
        return list(DEFAULT_GUARDED_DOCTYPES)
    doctypes = [line.strip() for line in raw.splitlines() if line.strip()]
    return doctypes or list(DEFAULT_GUARDED_DOCTYPES)


def is_guarded(doctype):
    """Return ``True`` when ``doctype`` is in the guarded list."""
    return doctype in get_guarded_doctypes()


def get_phone_config():
    """Return ``(default_country_code, national_number_length, default_region)``.

    * ``default_country_code`` - numeric dialing code (e.g. ``"91"``), used by
      the built-in fallback normalizer.
    * ``national_number_length`` - expected local-number length (e.g. ``10``).
    * ``default_region`` - ISO 3166-1 alpha-2 code (e.g. ``"IN"``), used by the
      ``phonenumbers`` library to interpret numbers typed without a country code.
    """
    settings = get_settings()
    country_code = (settings.get("default_country_code") or "91").strip() or "91"
    length = settings.get("national_number_length")
    try:
        length = int(length)
    except (TypeError, ValueError):
        length = 10
    if length <= 0:
        length = 10
    region = (settings.get("default_region") or "IN").strip().upper() or "IN"
    return country_code, length, region


def get_check_flags():
    """Return which value types are checked, as a dict of booleans.

    ``{"name": bool, "phone": bool, "email": bool}``. When the relevant setting
    is unset we default to ``True`` (check everything) so the app is safe by
    default.
    """
    settings = get_settings()

    def _flag(fieldname):
        value = settings.get(fieldname)
        # An unset checkbox on a brand-new settings doc reads as None -> treat
        # as enabled so the guard is active out of the box.
        return True if value is None else bool(value)

    return {
        "name": _flag("check_names"),
        "phone": _flag("check_phones"),
        "email": _flag("check_emails"),
    }


def get_check_types(doctype):
    """Return the set of value types to collect for ``doctype``.

    Combines two things:

    1. the per-DocType map :data:`DEFAULT_CHECK_TYPES` (e.g. Customer contributes
       only ``Name``; Contact only ``Phone``/``Email``);
    2. the global on/off flags from settings (``check_names`` / ``check_phones``
       / ``check_emails``), which can switch a whole type off site-wide.

    A DocType not present in the map defaults to all three types (then still
    filtered by the global flags).

    :returns: a set drawn from ``{"Name", "Phone", "Email"}``.
    """
    allowed = DEFAULT_CHECK_TYPES.get(doctype, {"Name", "Phone", "Email"})

    flags = get_check_flags()
    globally_on = set()
    if flags["name"]:
        globally_on.add("Name")
    if flags["phone"]:
        globally_on.add("Phone")
    if flags["email"]:
        globally_on.add("Email")

    return allowed & globally_on


def get_function_map():
    """Return ``{doctype: function}`` mapping, from settings or the default.

    The settings field ``function_scopes`` is free text, one function per line::

        Sales: Lead, Customer
        Purchase: Supplier
        HR: Employee

    Falls back to :data:`DEFAULT_FUNCTION_BY_DOCTYPE` when blank.
    """
    raw = (get_settings().get("function_scopes") or "").strip()
    if not raw:
        return dict(DEFAULT_FUNCTION_BY_DOCTYPE)

    mapping = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        function, doctypes = line.split(":", 1)
        function = function.strip()
        for dt in doctypes.split(","):
            dt = dt.strip()
            if dt and function:
                mapping[dt] = function
    return mapping or dict(DEFAULT_FUNCTION_BY_DOCTYPE)


def get_scopes(doc):
    """Return the set of function scopes a document belongs to.

    * A party DocType (Lead/Customer/Supplier/Employee) maps to its single
      function. A guarded DocType with no configured function falls back to a
      scope named after the DocType itself (its own private bucket).
    * A Contact takes the scope(s) of every party it links to (from its ``links``
      child table). A Contact with no known-function link falls back to
      :data:`CONTACT_FALLBACK_SCOPE`.

    Duplicate detection only matches within a shared scope, so the same value in
    two different functions never collides.
    """
    function_map = get_function_map()
    doctype = doc.doctype

    if doctype == "Contact":
        scopes = set()
        for link in (doc.get("links") or []):
            link_doctype = link.get("link_doctype") if hasattr(link, "get") else getattr(link, "link_doctype", None)
            if link_doctype in function_map:
                scopes.add(function_map[link_doctype])
        return scopes or {CONTACT_FALLBACK_SCOPE}

    return {function_map.get(doctype, doctype)}

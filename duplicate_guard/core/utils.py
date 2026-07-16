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
#   Employee            -> name/phone/email live directly on the record; all
#                          three are enforced among ACTIVE employees only, and
#                          company-domain emails are exempt (see below). Name
#                          checking can be switched off in Settings.
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
# These are the FALLBACK defaults. The live values are read from *Duplicate
# Guard Settings* (see :func:`get_employee_active_statuses` and
# :func:`get_employee_exempt_email_domains`) so that no site ever has to edit
# Python to configure them.

# Employee statuses that count as "currently employed". Name / phone / personal
# email uniqueness is enforced ONLY against employees in one of these statuses,
# so a resigned person's details free up for reuse (including by the same person
# rejoining under a new Employee record). ERPNext's Employee.status options are
# "Active", "Inactive", "Suspended" and "Left".
DEFAULT_EMPLOYEE_ACTIVE_STATUSES = {"Active"}

# Email domains exempt from duplicate checks *for Employees*. Official company
# addresses are legitimately shared and reassigned (a resigned employee's
# address is handed to a new hire), so they are never indexed and never blocked.
# Every OTHER (personal) employee email must still be unique among active
# employees. Empty by default: a site declares its own domains in Settings.
DEFAULT_EMPLOYEE_EXEMPT_EMAIL_DOMAINS = set()


def _settings_tokens(fieldname):
    """Return the non-empty tokens of a comma/newline separated setting."""
    raw = (get_settings().get(fieldname) or "").strip()
    if not raw:
        return []
    tokens = []
    for line in raw.replace(",", "\n").splitlines():
        token = line.strip()
        if token:
            tokens.append(token)
    return tokens


def get_employee_active_statuses():
    """Return the Employee statuses that reserve a person's details.

    Read from the ``employee_active_statuses`` setting (one status per line, or
    comma separated). Falls back to
    :data:`DEFAULT_EMPLOYEE_ACTIVE_STATUSES` when blank.
    """
    tokens = _settings_tokens("employee_active_statuses")
    return set(tokens) or set(DEFAULT_EMPLOYEE_ACTIVE_STATUSES)


def get_employee_exempt_email_domains():
    """Return the company email domains that Employees may legitimately share.

    Read from the ``employee_email_exempt_domains`` setting. Values are
    lower-cased and a leading ``@`` is tolerated, so both ``example.com`` and
    ``@example.com`` work. Blank means "no employee email is exempt".
    """
    tokens = _settings_tokens("employee_email_exempt_domains")
    return {t.lower().lstrip("@") for t in tokens} or set(
        DEFAULT_EMPLOYEE_EXEMPT_EMAIL_DOMAINS
    )


# ---------------------------------------------------------------------------
# Phone region resolution
# ---------------------------------------------------------------------------
# A phone number typed without a country code is ambiguous: "9876543210" is a
# valid national number in several countries. Normalizing every such number with
# one site-wide region silently mislabels foreign numbers - an Indian site with
# a UK lead would turn "07911 123456" into an Indian E.164 number, which can
# both miss real duplicates and invent false ones.
#
# So the region is resolved PER RECORD, from the record's own country field when
# it has one, falling back to the site default in settings. Numbers that already
# carry an explicit country code are unaffected - they parse on their own.

# Fieldnames that hold a Link to the Country DocType on a party record.
_COUNTRY_FIELDNAMES = ("country",)

# Party DocTypes a Contact may borrow its country from, in preference order.
_CONTACT_COUNTRY_SOURCES = ("Customer", "Supplier", "Lead")

# ISO 3166-1 alpha-2 -> international dialing code. Used only by the built-in
# fallback normalizer; when the ``phonenumbers`` library is installed it derives
# this itself. Not exhaustive - unlisted regions fall back to the site default.
ISO_TO_DIALING_CODE = {
    "AE": "971", "AR": "54", "AT": "43", "AU": "61", "BD": "880", "BE": "32",
    "BR": "55", "CA": "1", "CH": "41", "CL": "56", "CN": "86", "CO": "57",
    "CZ": "420", "DE": "49", "DK": "45", "EG": "20", "ES": "34", "FI": "358",
    "FR": "33", "GB": "44", "GR": "30", "HK": "852", "ID": "62", "IE": "353",
    "IL": "972", "IN": "91", "IT": "39", "JP": "81", "KE": "254", "KR": "82",
    "LK": "94", "MX": "52", "MY": "60", "NG": "234", "NL": "31", "NO": "47",
    "NP": "977", "NZ": "64", "OM": "968", "PH": "63", "PK": "92", "PL": "48",
    "PT": "351", "QA": "974", "RO": "40", "RU": "7", "SA": "966", "SE": "46",
    "SG": "65", "TH": "66", "TR": "90", "TW": "886", "UA": "380", "US": "1",
    "VN": "84", "ZA": "27",
}

# ISO 3166-1 alpha-2 -> usual national (subscriber) number length. Used by the
# fallback normalizer to decide whether a bare number looks complete. Unlisted
# regions fall back to the site-wide setting.
NATIONAL_LENGTH_BY_REGION = {
    "AE": 9, "AU": 9, "BD": 10, "BR": 11, "CA": 10, "CN": 11, "DE": 10,
    "EG": 10, "ES": 9, "FR": 9, "GB": 10, "HK": 8, "ID": 10, "IN": 10,
    "IT": 10, "JP": 10, "KE": 9, "LK": 9, "MY": 9, "NG": 10, "NL": 9,
    "NP": 10, "NZ": 9, "PH": 10, "PK": 10, "QA": 8, "SA": 9, "SG": 8,
    "TH": 9, "TR": 10, "US": 10, "VN": 9, "ZA": 9,
}


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
    """Return the site-wide ``(country_code, national_length, region)`` defaults.

    Used when a record gives us nothing better to go on. See
    :func:`get_phone_config_for` for the per-record version, which is what the
    engine actually calls.

    * ``country_code`` - numeric dialing code (e.g. ``"91"``), used by the
      built-in fallback normalizer.
    * ``national_length`` - expected local-number length (e.g. ``10``).
    * ``region`` - ISO 3166-1 alpha-2 code (e.g. ``"IN"``), used by the
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


def _doctype_has_field(doctype, fieldname):
    """Return ``True`` when ``doctype`` really has ``fieldname``.

    Guards against querying a column that does not exist on this ERPNext
    version (``Customer`` has no ``country`` field, for instance).
    """
    try:
        return bool(frappe.get_meta(doctype).get_field(fieldname))
    except Exception:
        return False


def _country_of_doc(doc):
    """Return the Country name held on ``doc`` itself, or ``None``."""
    for fieldname in _COUNTRY_FIELDNAMES:
        value = doc.get(fieldname)
        if value:
            return value
    return None


def _country_of_contact(doc):
    """Return a Country borrowed from a Contact's linked parties, or ``None``.

    A Contact has no country of its own, so we take it from the party it belongs
    to - a UK supplier's contact should have UK numbers read as UK numbers.
    """
    links = []
    for link in (doc.get("links") or []):
        link_doctype = link.get("link_doctype") if hasattr(link, "get") else getattr(link, "link_doctype", None)
        link_name = link.get("link_name") if hasattr(link, "get") else getattr(link, "link_name", None)
        if link_doctype and link_name:
            links.append((link_doctype, link_name))

    for preferred in _CONTACT_COUNTRY_SOURCES:
        for link_doctype, link_name in links:
            if link_doctype != preferred:
                continue
            if not _doctype_has_field(link_doctype, "country"):
                continue
            country = frappe.db.get_value(link_doctype, link_name, "country")
            if country:
                return country
    return None


def get_region_for(doc):
    """Return the ISO 3166-1 alpha-2 region to read ``doc``'s phone numbers in.

    Resolution order:

    1. the record's own ``country`` field (Lead, Supplier, ...);
    2. for a Contact, the country of the party it links to;
    3. the site-wide ``default_region`` setting.

    :returns: an upper-case two-letter code, e.g. ``"IN"`` or ``"GB"``.
    """
    _, _, default_region = get_phone_config()

    country = _country_of_doc(doc)
    if not country and doc.doctype == "Contact":
        country = _country_of_contact(doc)
    if not country:
        return default_region

    try:
        code = frappe.get_cached_value("Country", country, "code")
    except Exception:
        code = None

    if not code:
        return default_region
    return code.strip().upper() or default_region


def get_phone_config_for(doc):
    """Return ``(country_code, national_length, region)`` tailored to ``doc``.

    Same shape as :func:`get_phone_config`, but the region comes from the
    record's country when it has one, and the dialing code / national length
    follow from that region. Anything we cannot resolve falls back to the
    site-wide settings, so a single-country site behaves exactly as before.
    """
    default_code, default_length, default_region = get_phone_config()

    region = get_region_for(doc)
    if region == default_region:
        return default_code, default_length, region

    country_code = ISO_TO_DIALING_CODE.get(region, default_code)
    national_length = NATIONAL_LENGTH_BY_REGION.get(region, default_length)
    return country_code, national_length, region


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


def _employee_name_check_enabled():
    """Return ``True`` when Employee names should be duplicate-checked.

    Controlled by the ``check_employee_names`` setting. Defaults to enabled when
    the field is absent (an older Settings record), matching the documented
    behaviour.
    """
    value = get_settings().get("check_employee_names")
    return True if value is None else bool(value)


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
    allowed = set(DEFAULT_CHECK_TYPES.get(doctype, {"Name", "Phone", "Email"}))

    # Employee name checking is separately switchable: two genuinely different
    # active people can share a name, which is common in many regions, so a site
    # must be able to turn this off without losing phone/email checking.
    if doctype == "Employee" and not _employee_name_check_enabled():
        allowed.discard("Name")

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

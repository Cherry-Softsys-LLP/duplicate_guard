"""
duplicate_guard.core.normalizer
==============================================

The normalization engine.

Everything in this module is a **pure function**: text in, text out. It does not
touch the database, the current user, or any Frappe global. Pure functions are
trivial to unit-test and behave identically no matter where they are called from
(UI, REST, Data Import, a background job, ``bench execute`` ...).

Three kinds of values are normalized:

* names   -> :func:`normalize_name`
* emails  -> :func:`normalize_email`
* phones  -> :func:`normalize_phone`

Two values are duplicates if, and only if, their normalized forms are equal.

Phone numbers: country-code aware (E.164)
----------------------------------------
Phone numbers are normalized to **E.164** international form, e.g.
``+919876543210``. The country code is always part of the canonical value:

* kept when the user typed one (``+91 ...`` / a ``+`` prefix / a ``00`` prefix);
* inferred from the configured default region when the number is bare.

This is what stops two *different* countries that happen to share the same
national digits (``+91 9876543210`` in India vs ``+1 9876543210`` in the USA)
from being flagged as duplicates, while still matching the same Indian number
whether it was entered as ``+91 9876543210``, ``09876543210`` or ``9876543210``.

When Google's ``libphonenumber`` port (the ``phonenumbers`` package) is
installed, it is used for gold-standard, per-country parsing. If it is not
available, a self-contained fallback produces the same E.164 shape for the
common cases so the app still works.
"""

import re

# ---------------------------------------------------------------------------
# Optional dependency: Google's libphonenumber (Python port).
# We import it defensively so the engine still runs if it is not installed.
# ---------------------------------------------------------------------------
try:
    import phonenumbers as _phonenumbers
except Exception:  # pragma: no cover - exercised only when the package is absent
    _phonenumbers = None

# Pre-compiled regular expressions (compiled once at import for speed).
_WHITESPACE_RE = re.compile(r"\s+")      # one or more whitespace characters
_NON_DIGIT_RE = re.compile(r"\D+")       # one or more NON-digit characters


def normalize_name(value):
    """Normalize a business / person name for duplicate comparison.

    Rules: lower case, trim, collapse internal whitespace.

    >>> normalize_name("ABC   Industries")
    'abc industries'
    """
    if value is None:
        return ""
    text = str(value)
    text = text.strip().lower()
    text = _WHITESPACE_RE.sub(" ", text)
    return text


def normalize_email(value):
    """Normalize an email address: trim + lower case.

    >>> normalize_email("Sales@ABC.com")
    'sales@abc.com'
    """
    if value is None:
        return ""
    return str(value).strip().lower()


def normalize_phone(
    value,
    default_country_code="91",
    national_number_length=10,
    default_region="IN",
):
    """Normalize a phone number to canonical E.164 form (country code included).

    Examples (default region India)
    -------------------------------
    >>> normalize_phone("+91 9876543210")
    '+919876543210'
    >>> normalize_phone("09876543210")
    '+919876543210'
    >>> normalize_phone("98765 43210")
    '+919876543210'
    >>> normalize_phone("+1 9876543210")   # USA - stays distinct
    '+19876543210'

    :param value: the raw phone number (may be ``None``).
    :param default_country_code: numeric dialing code without ``+`` used by the
        built-in fallback when a bare number has no country code (India ``"91"``).
    :param national_number_length: expected local-number length, used by the
        fallback to detect a bare number that already embeds its country code.
    :param default_region: ISO 3166-1 alpha-2 region (e.g. ``"IN"``, ``"US"``,
        ``"GB"``) used by ``phonenumbers`` to interpret bare numbers.
    :returns: E.164 string like ``"+919876543210"``, or ``""`` when empty.
    """
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""

    # Preferred path: real phone parsing when the library is available.
    if _phonenumbers is not None:
        result = _normalize_with_library(raw, default_region)
        if result:
            return result
        # If the library could not parse it, fall through to the built-in logic
        # so we still return a consistent value rather than nothing.

    return _normalize_builtin(raw, default_country_code, national_number_length)


def _normalize_with_library(raw, default_region):
    """Parse with libphonenumber and format as E.164. ``""`` on failure."""
    region = (default_region or "").strip().upper() or None
    try:
        parsed = _phonenumbers.parse(raw, region)
    except _phonenumbers.NumberParseException:
        return ""
    try:
        # We intentionally do NOT require is_valid_number(): we want to normalize
        # even "unassigned" numbers consistently for duplicate detection. E.164
        # formatting still yields a stable +CC+national string.
        return _phonenumbers.format_number(
            parsed, _phonenumbers.PhoneNumberFormat.E164
        )
    except Exception:  # pragma: no cover - defensive
        return ""


def _normalize_builtin(raw, default_country_code, national_number_length):
    """Dependency-free E.164 normalization for the common cases.

    Logic
    -----
    * A leading ``+`` (or an international ``00`` prefix) means the country code
      is already present -> keep everything: ``+`` followed by all digits.
    * Otherwise the number is bare -> strip the trunk/STD leading zero and
      prepend the default country code. If the bare number already embeds the
      default country code (e.g. ``919876543210``), we do not add it twice.
    """
    has_plus = raw.startswith("+")
    digits = _NON_DIGIT_RE.sub("", raw)
    if not digits:
        return ""

    # Treat a leading international access code "00" like a "+".
    if not has_plus and digits.startswith("00"):
        has_plus = True
        digits = digits[2:]
        if not digits:
            return ""

    country_code = _NON_DIGIT_RE.sub("", str(default_country_code or ""))

    if has_plus:
        # Country code already included by the user.
        return "+" + digits

    # Bare number: remove the trunk/STD leading zero(s).
    national = digits.lstrip("0") or digits

    # If the bare number is long enough to already contain the default country
    # code, strip that leading code so we do not double it.
    if (
        country_code
        and national_number_length
        and len(national) > national_number_length
        and national.startswith(country_code)
    ):
        candidate = national[len(country_code):]
        if len(candidate) >= national_number_length:
            national = candidate

    if country_code:
        return "+" + country_code + national
    return national

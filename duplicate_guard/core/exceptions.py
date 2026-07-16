"""
duplicate_guard.core.exceptions
==============================================

Custom exception types used across the duplicate-guard engine.

Why we define our own exceptions
--------------------------------
Frappe raises ``frappe.ValidationError`` for most validation problems. If we
raised that generic error, callers (tests, import routines, other apps) would
not be able to tell "this failed because of a duplicate" apart from "this failed
because a mandatory field was missing".

By subclassing ``frappe.ValidationError`` we get two things at once:

1. Frappe still treats our errors as normal validation errors, so the ERP UI,
   the REST API and the Data Import tool all show them to the user the way they
   show any other validation message (a red popup / an error row).
2. Our own code and our tests can catch the *specific* exception type
   (``DuplicateError``) and react to it precisely.
"""

import frappe


class DuplicateGuardError(frappe.ValidationError):
    """Base class for every error raised by this app."""


class DuplicateError(DuplicateGuardError):
    """Raised when a record collides with an existing Customer/Lead value."""


class DuplicateLegacyError(DuplicateGuardError):
    """Raised when a legacy_id already exists on another record.

    Legacy imports are expected to go through the upsert API
    (``duplicate_guard.api.upsert_by_legacy_id``). If a plain insert tries
    to create a second record that reuses a legacy id, we block it with this
    error instead of silently creating a duplicate.
    """


class ConfigurationError(DuplicateGuardError):
    """Raised when the app is mis-configured (e.g. an unknown guarded DocType)."""

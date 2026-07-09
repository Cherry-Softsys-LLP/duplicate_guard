"""
duplicate_guard.handlers.customer
=====================================

Document-event handlers for the **Customer** DocType.

These functions are wired up in ``hooks.py`` under ``doc_events["Customer"]``.
They are intentionally thin: all the real logic lives in the reusable engine
(:mod:`duplicate_guard.core`). Keeping the handlers small means
adding a new guarded DocType later is a copy-paste of a couple of lines, not a
re-implementation.
"""

from duplicate_guard.core import validator
from duplicate_guard.handlers.common import guard_legacy_id


def validate_customer(doc, method=None):
    """``validate`` handler for Customer.

    Runs the full duplicate check. Lead-conversion handling (ignoring only the
    originating Lead) is done inside :func:`validator.validate_document` by
    reading ``Customer.lead_name``, so nothing special is needed here.
    """
    validator.validate_document(doc)


def before_insert_customer(doc, method=None):
    """``before_insert`` handler for Customer: the legacy-id safety net."""
    guard_legacy_id(doc, method)

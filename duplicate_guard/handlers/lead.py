"""
duplicate_guard.handlers.lead
=================================

Document-event handlers for the **Lead** DocType.

Wired up in ``hooks.py`` under ``doc_events["Lead"]``. Like the Customer
handler, these are thin wrappers over the shared engine.
"""

from duplicate_guard.core import validator
from duplicate_guard.handlers.common import guard_legacy_id


def validate_lead(doc, method=None):
    """``validate`` handler for Lead: run the full duplicate check.

    A Lead whose status is already ``Converted`` is skipped: its phone/email have
    moved onto the new Customer's Contact, so re-checking a converted Lead would
    only produce a false clash against that Contact. The check already ran when
    the Lead was first created, so nothing is lost.
    """
    if (doc.get("status") or "") == "Converted":
        return
    validator.validate_document(doc)


def before_insert_lead(doc, method=None):
    """``before_insert`` handler for Lead: the legacy-id safety net."""
    guard_legacy_id(doc, method)

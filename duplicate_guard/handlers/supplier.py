"""
duplicate_guard.handlers.supplier
=====================================

Document-event handler for the **Supplier** DocType (the Purchase function).

Like a Customer, a Supplier's phone/email live on its linked Contacts, so the
Supplier itself is checked for name uniqueness only; the phone/email dedup for
Purchase happens through those Contacts (which take the Purchase scope from their
link to the Supplier).
"""

from duplicate_guard.core import validator


def validate_supplier(doc, method=None):
    """``validate`` handler for Supplier: run the duplicate check."""
    validator.validate_document(doc)

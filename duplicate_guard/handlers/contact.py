"""
duplicate_guard.handlers.contact
====================================

Document-event handler for the **Contact** DocType.

In ERPNext a Customer's phone numbers and email addresses do not live on the
Customer record - they live on linked Contact records (in the Contact's
``phone_nos`` / ``email_ids`` child tables). Guarding Contact is therefore what
makes phone/email duplicate detection work for Customers.

Contacts are checked for phone/email only (never name - a Contact is a person,
and people legitimately share names). The value-type restriction is configured
in :data:`duplicate_guard.core.utils.DEFAULT_CHECK_TYPES`, and the
"don't clash with my own linked party" logic lives in the validator's exclusion
builder, so this handler stays a thin wrapper.
"""

from duplicate_guard.core import validator


def validate_contact(doc, method=None):
    """``validate`` handler for Contact: run the phone/email duplicate check."""
    validator.validate_document(doc)

"""
duplicate_guard.handlers.employee
=====================================

Document-event handler for the **Employee** DocType (the HR function).

Unlike Customers/Suppliers, an Employee carries its contact details directly
(``cell_number``, ``personal_email``, ``company_email`` ...), so phone/email are
read straight off the Employee. Employees are not deduped by name (people share
names); only phone/email are checked, and only within the HR function.
"""

from duplicate_guard.core import validator


def validate_employee(doc, method=None):
    """``validate`` handler for Employee: run the phone/email duplicate check."""
    validator.validate_document(doc)

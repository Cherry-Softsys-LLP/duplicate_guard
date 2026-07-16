"""
duplicate_guard.handlers.employee
=====================================

Document-event handler for the **Employee** DocType (the HR function).

Unlike Customers/Suppliers, an Employee carries its contact details directly
(``cell_number``, ``personal_email``, ``company_email`` ...), so name, phone and
email are all read straight off the Employee, and matched only within the HR
function.

Two HR-specific rules apply, both configured in *Duplicate Guard Settings* and
implemented in :mod:`duplicate_guard.core.search`:

* **Only active employees participate.** A person who has left is neither indexed
  nor checked, so their name/phone/personal email free up - which is what lets
  the same person rejoin under a new Employee record.
* **Official company email domains are exempt.** A shared mailbox
  (``accounts@example.com``) may sit on several employees and be reassigned from
  a leaver to a new hire, so addresses on the configured domains are never
  indexed or blocked. Personal addresses are still enforced.

Employee name checking can be switched off entirely (``check_employee_names``)
for organisations where two different active people may share a name.
"""

from duplicate_guard.core import validator


def validate_employee(doc, method=None):
    """``validate`` handler for Employee: run the phone/email duplicate check."""
    validator.validate_document(doc)

"""Controller for the *Duplicate Report* DocType.

Each record documents one duplicate collision that was allowed through while
Migration Mode was active, so administrators can clean the data up afterwards.
"""

from frappe.model.document import Document


class DuplicateReport(Document):
    """A single logged duplicate collision (audit trail for Migration Mode)."""

    pass

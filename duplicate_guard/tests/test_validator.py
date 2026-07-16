"""
Integration tests for the duplicate-guard engine.

These run against a real test site (they create Customers and Leads), so use::

    bench --site yoursite run-tests --app duplicate_guard

FrappeTestCase wraps each test method in a database transaction that is rolled
back afterwards, so the records created here never persist.
"""

import unittest

import frappe

# Frappe v16 renamed the base test class: FrappeTestCase (v15) is deprecated in
# favour of IntegrationTestCase (v16). Import the v16 class, falling back to the
# v15 name so this file runs on either line. Both give per-test transaction
# rollback and the same setUpClass/setUp semantics used below.
try:  # Frappe v16+
    from frappe.tests import IntegrationTestCase as _BaseTestCase
except ImportError:  # Frappe v15
    from frappe.tests.utils import FrappeTestCase as _BaseTestCase

from duplicate_guard import api
from duplicate_guard.core import metadata
from duplicate_guard.setup.install import legacy_id_enabled
from duplicate_guard.core.exceptions import (
    DuplicateError,
    DuplicateLegacyError,
)

SETTINGS = "Duplicate Guard Settings"


def apply_settings(**overrides):
    """Set settings fields and refresh caches so changes take effect at once."""
    values = {
        "enabled": 1,
        "strict_mode": 1,
        "migration_mode": 0,
        "check_names": 1,
        "check_phones": 1,
        "check_emails": 1,
        "default_country_code": "91",
        "national_number_length": 10,
        "guarded_doctypes": "Customer\nLead\nSupplier\nEmployee\nContact",
        "function_scopes": "Sales: Lead, Customer\nPurchase: Supplier\nHR: Employee",
        "name_field_overrides": "",
        "phone_field_overrides": "",
        "email_field_overrides": "",
        "ignored_fields": "",
        "employee_active_statuses": "Active",
        "employee_email_exempt_domains": "example-corp.com",
        "check_employee_names": 1,
    }
    values.update(overrides)
    settings = frappe.get_doc(SETTINGS)
    settings.update(values)
    settings.save(ignore_permissions=True)
    frappe.clear_document_cache(SETTINGS, SETTINGS)
    metadata.clear_field_cache()


def make_customer(customer_name, **extra):
    """Create and insert a Customer with the given name and extra fields."""
    doc = frappe.new_doc("Customer")
    doc.customer_name = customer_name
    doc.customer_type = "Company"
    doc.customer_group = _root("Customer Group")
    doc.territory = _root("Territory")
    doc.update(extra)
    doc.insert()
    return doc


def make_lead(company_name, first_name=None, **extra):
    """Create and insert a Lead. ``first_name`` defaults to a unique value so
    person-name matching never interferes with organization-name tests."""
    doc = frappe.new_doc("Lead")
    doc.company_name = company_name
    doc.first_name = first_name or frappe.generate_hash(length=8)
    doc.update(extra)
    doc.insert()
    return doc


def make_contact(first_name=None, phone=None, email=None, link_doctype=None, link_name=None):
    """Create and insert a Contact, optionally with a phone/email and a party link.

    Phone and email are stored in the Contact's child tables (``phone_nos`` /
    ``email_ids``), exactly as ERPNext stores them, so the child-table discovery
    is exercised.
    """
    doc = frappe.new_doc("Contact")
    doc.first_name = first_name or ("CT-" + frappe.generate_hash(length=6))
    if phone:
        doc.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1, "is_primary_phone": 1})
    if email:
        doc.append("email_ids", {"email_id": email, "is_primary": 1})
    if link_doctype and link_name:
        doc.append("links", {"link_doctype": link_doctype, "link_name": link_name})
    doc.insert()
    return doc


def make_supplier(supplier_name):
    """Create and insert a Supplier (Purchase function)."""
    doc = frappe.new_doc("Supplier")
    doc.supplier_name = supplier_name
    doc.supplier_group = _root("Supplier Group")
    doc.insert()
    return doc


def _root(doctype):
    """Return the root node of a nested-set tree DocType (its ``lft`` is 1),
    e.g. the root Customer Group / Territory that always exists on an ERPNext
    site. Falls back to any record if the tree is not nested-set."""
    name = frappe.db.get_value(doctype, {"lft": 1}, "name")
    return name or frappe.db.get_value(doctype, {}, "name")


class DuplicateGuardTestBase(_BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Nothing to set up at the class level: phone/email for Customers are
        # exercised through real Contact records (see make_contact), and the
        # standard Lead/Contact/Customer masters already exist on an ERPNext
        # test site.

    def setUp(self):
        apply_settings()
        metadata.clear_field_cache()


class TestNameUniqueness(DuplicateGuardTestBase):
    def test_customer_name_unique(self):
        make_customer("ABC Industries")
        with self.assertRaises(DuplicateError):
            make_customer("ABC Industries")

    def test_customer_name_unique_after_normalization(self):
        make_customer("ABC Industries")
        with self.assertRaises(DuplicateError):
            make_customer("  abc    INDUSTRIES ")

    def test_lead_name_unique(self):
        make_lead("XYZ Traders")
        with self.assertRaises(DuplicateError):
            make_lead("XYZ Traders")

    def test_cross_doctype_customer_then_lead(self):
        make_customer("ABC Industries")
        with self.assertRaises(DuplicateError):
            make_lead("ABC Industries")

    def test_cross_doctype_lead_then_customer(self):
        make_lead("ABC Industries")
        with self.assertRaises(DuplicateError):
            make_customer("ABC Industries")


class TestPhoneMatching(DuplicateGuardTestBase):
    def test_phone_duplicate_same_field(self):
        make_lead("Alpha Corp", mobile_no="+91 9876543210")
        with self.assertRaises(DuplicateError):
            make_lead("Beta Corp", mobile_no="09876543210")

    def test_phone_duplicate_cross_field(self):
        # Existing lead's number in mobile_no; new lead reuses it in phone.
        make_lead("Alpha Corp", mobile_no="9876543210")
        with self.assertRaises(DuplicateError):
            make_lead("Beta Corp", phone="98765 43210")

    def test_phone_duplicate_cross_doctype(self):
        # A Sales-scoped Contact (linked to a Customer) collides with a Lead.
        cust = make_customer("Alpha Ltd")
        make_contact(phone="9876543210", link_doctype="Customer", link_name=cust.name)
        with self.assertRaises(DuplicateError):
            make_lead("Beta Corp", mobile_no="+91-9876543210")

    def test_same_record_repeated_phone_is_valid(self):
        # Same number in mobile_no AND phone on ONE record -> valid, no error.
        lead = make_lead("Alpha Corp", mobile_no="9876543210", phone="9876543210")
        self.assertTrue(lead.name)

    def test_different_country_same_national_digits_not_duplicate(self):
        # The country-code fix, end to end: an Indian and a US number that share
        # the same 10 national digits must NOT be treated as duplicates.
        make_lead("India Org", mobile_no="+91 9876543210")
        us_lead = make_lead("US Org", mobile_no="+1 9876543210")  # must NOT raise
        self.assertTrue(us_lead.name)


class TestEmailMatching(DuplicateGuardTestBase):
    def test_email_duplicate(self):
        make_lead("Alpha Corp", email_id="Sales@ABC.com")
        with self.assertRaises(DuplicateError):
            make_lead("Beta Corp", email_id="sales@abc.com")


class TestEditingRecords(DuplicateGuardTestBase):
    def test_resaving_same_record_is_valid(self):
        lead = make_lead("Alpha Corp", mobile_no="9876543210")
        # Re-saving must ignore the record's own values.
        lead.reload()
        lead.save()
        self.assertTrue(lead.name)

    def test_editing_to_collide_is_blocked(self):
        make_lead("Alpha Corp", mobile_no="9876543210")
        other = make_lead("Beta Corp", mobile_no="9000000000")
        other.mobile_no = "9876543210"
        with self.assertRaises(DuplicateError):
            other.save()


class TestLeadConversion(DuplicateGuardTestBase):
    def test_originating_lead_is_ignored(self):
        # Customer name matches the originating Lead's org name; excluded via
        # lead_name, so conversion is allowed.
        lead = make_lead("ABC Industries", mobile_no="9876543210")
        customer = frappe.new_doc("Customer")
        customer.customer_name = "ABC Industries"
        customer.customer_type = "Company"
        customer.customer_group = _root("Customer Group")
        customer.territory = _root("Territory")
        customer.lead_name = lead.name  # originating lead link -> excluded
        customer.insert()  # must NOT raise
        self.assertTrue(customer.name)

    def test_other_leads_still_checked_during_conversion(self):
        lead = make_lead("ABC Industries", mobile_no="9876543210")
        # A DIFFERENT lead owns an org name the new Customer will also try to use.
        make_lead("Conflict Org")

        customer = frappe.new_doc("Customer")
        customer.customer_name = "Conflict Org"  # collides with the other lead
        customer.customer_type = "Company"
        customer.customer_group = _root("Customer Group")
        customer.territory = _root("Territory")
        customer.lead_name = lead.name  # excludes only the originating lead
        with self.assertRaises(DuplicateError):
            customer.insert()

    def test_contact_from_conversion_not_blocked_by_originating_lead(self):
        # Simulate conversion: Lead -> Customer (linked) -> Contact carrying the
        # lead's phone, linked to the Customer. The Contact must NOT be flagged
        # against the originating Lead's identical phone.
        lead = make_lead("ABC Industries", mobile_no="9876543210")
        customer = frappe.new_doc("Customer")
        customer.customer_name = "ABC Industries Pvt Ltd"
        customer.customer_type = "Company"
        customer.customer_group = _root("Customer Group")
        customer.territory = _root("Territory")
        customer.lead_name = lead.name
        customer.insert()

        contact = make_contact(
            phone="9876543210", link_doctype="Customer", link_name=customer.name
        )
        self.assertTrue(contact.name)


class TestContact(DuplicateGuardTestBase):
    def test_contact_phone_duplicate_across_parties(self):
        c1 = make_customer("Alpha Ltd")
        make_contact(phone="9876543210", link_doctype="Customer", link_name=c1.name)
        c2 = make_customer("Beta Ltd")
        with self.assertRaises(DuplicateError):
            make_contact(phone="+91 9876543210", link_doctype="Customer", link_name=c2.name)

    def test_contact_email_duplicate(self):
        make_contact(email="Sales@ABC.com")
        with self.assertRaises(DuplicateError):
            make_contact(email="sales@abc.com")

    def test_contact_phone_matches_lead_phone(self):
        # A Sales-scoped Contact (linked to a Customer) collides with a Lead's
        # phone; both are in the Sales function.
        make_lead("Some Org", mobile_no="9876543210")
        cust = make_customer("Some Org Ltd")
        with self.assertRaises(DuplicateError):
            make_contact(phone="9876543210", link_doctype="Customer", link_name=cust.name)

    def test_contact_not_blocked_by_its_own_linked_lead(self):
        # A Contact linked to a Lead whose own phone is identical is the same
        # entity, so it must not be flagged.
        lead = make_lead("Some Org", mobile_no="9876543210")
        contact = make_contact(
            phone="9876543210", link_doctype="Lead", link_name=lead.name
        )
        self.assertTrue(contact.name)

    def test_contact_same_number_twice_in_grid_is_valid(self):
        doc = frappe.new_doc("Contact")
        doc.first_name = "Grid Test"
        doc.append("phone_nos", {"phone": "9876543210", "is_primary_mobile_no": 1})
        doc.append("phone_nos", {"phone": "+91 9876543210"})  # same number, formatted differently
        doc.insert()  # must NOT raise (same value within one record)
        self.assertTrue(doc.name)


class TestFunctionScoping(DuplicateGuardTestBase):
    def test_scope_resolution(self):
        from duplicate_guard.core.utils import get_scopes

        self.assertEqual(get_scopes(frappe.new_doc("Lead")), {"Sales"})
        self.assertEqual(get_scopes(frappe.new_doc("Customer")), {"Sales"})
        self.assertEqual(get_scopes(frappe.new_doc("Supplier")), {"Purchase"})
        self.assertEqual(get_scopes(frappe.new_doc("Employee")), {"HR"})

        standalone = frappe.new_doc("Contact")
        self.assertEqual(get_scopes(standalone), {"Contact"})

        cust = make_customer("Scope Cust")
        c1 = frappe.new_doc("Contact")
        c1.append("links", {"link_doctype": "Customer", "link_name": cust.name})
        self.assertEqual(get_scopes(c1), {"Sales"})

        sup = make_supplier("Scope Supp")
        c2 = frappe.new_doc("Contact")
        c2.append("links", {"link_doctype": "Supplier", "link_name": sup.name})
        self.assertEqual(get_scopes(c2), {"Purchase"})

    def test_sales_and_purchase_do_not_collide(self):
        # Same phone in Sales (Lead) and Purchase (Supplier's Contact) is allowed.
        make_lead("Sales Org", mobile_no="9876543210")
        sup = make_supplier("Purchase Org")
        contact = make_contact(
            phone="9876543210", link_doctype="Supplier", link_name=sup.name
        )
        self.assertTrue(contact.name)  # must NOT raise

    def test_within_purchase_collides(self):
        s1 = make_supplier("Supplier One")
        make_contact(phone="9876543210", link_doctype="Supplier", link_name=s1.name)
        s2 = make_supplier("Supplier Two")
        with self.assertRaises(DuplicateError):
            make_contact(phone="+91 9876543210", link_doctype="Supplier", link_name=s2.name)

    def test_email_cross_function_allowed(self):
        # Same email for a Sales customer contact and a Purchase supplier contact.
        cust = make_customer("Dual Role Cust")
        make_contact(email="person@dual.com", link_doctype="Customer", link_name=cust.name)
        sup = make_supplier("Dual Role Supp")
        contact = make_contact(
            email="person@dual.com", link_doctype="Supplier", link_name=sup.name
        )
        self.assertTrue(contact.name)  # must NOT raise



    def test_migration_mode_allows_and_logs(self):
        apply_settings(migration_mode=1)
        make_lead("ABC Industries", mobile_no="9876543210")
        # This would normally be blocked, but Migration Mode lets it through.
        second = make_lead("ABC Industries", mobile_no="9876543210")
        self.assertTrue(second.name)
        # ... and records at least one report row.
        reports = frappe.get_all(
            "Duplicate Report",
            filters={"reference_doctype": "Lead", "reference_name": second.name},
        )
        self.assertGreaterEqual(len(reports), 1)


@unittest.skipUnless(
    legacy_id_enabled(),
    "The legacy id field is opt-in; set 'duplicate_guard_enable_legacy_id': 1 in "
    "site_config.json and run 'bench migrate' to exercise these tests.",
)
class TestLegacyImport(DuplicateGuardTestBase):
    def test_upsert_creates_then_updates(self):
        first = api.upsert_by_legacy_id(
            "Lead", {"company_name": "Legacy Org", "legacy_id": "LEG-1001"}
        )
        self.assertEqual(first["action"], "created")

        second = api.upsert_by_legacy_id(
            "Lead",
            {"company_name": "Legacy Org Renamed", "legacy_id": "LEG-1001"},
        )
        self.assertEqual(second["action"], "updated")
        self.assertEqual(first["name"], second["name"])

        # Exactly one Lead carries that legacy id.
        count = frappe.db.count("Lead", {"legacy_id": "LEG-1001"})
        self.assertEqual(count, 1)

    def test_plain_insert_reusing_legacy_id_is_blocked(self):
        make_lead("Legacy Org", legacy_id="LEG-2002")
        with self.assertRaises(DuplicateLegacyError):
            make_lead("Different Org", legacy_id="LEG-2002")


class TestCheckDuplicatesApi(DuplicateGuardTestBase):
    def test_probe_reports_collision_without_saving(self):
        make_lead("ABC Industries", mobile_no="9876543210")
        result = api.check_duplicates(
            "Lead", {"company_name": "ABC Industries", "mobile_no": "+91 9876543210"}
        )
        types = {row["value_type"] for row in result}
        self.assertIn("Name", types)
        self.assertIn("Phone", types)
        # Nothing was created by the probe (only the seed lead exists).
        self.assertEqual(
            frappe.db.count("Lead", {"company_name": "ABC Industries"}), 1
        )


class TestGuardDisabled(DuplicateGuardTestBase):
    def test_disabled_guard_allows_duplicates(self):
        apply_settings(enabled=0)
        make_lead("ABC Industries")
        second = make_lead("ABC Industries")  # no error when disabled
        self.assertTrue(second.name)


def make_employee(employee_name, status="Active", **extra):
    """Create and insert an Employee (HR function).

    ``date_of_birth`` / ``date_of_joining`` are mandatory on ERPNext's Employee,
    so they are defaulted here to keep the tests about duplicates rather than
    about HR paperwork.
    """
    doc = frappe.new_doc("Employee")
    doc.employee_name = employee_name
    doc.first_name = employee_name.split(" ")[0]
    doc.gender = frappe.db.get_value("Gender", {}, "name") or "Male"
    doc.date_of_birth = "1990-01-01"
    doc.date_of_joining = "2020-01-01"
    doc.status = status
    doc.company = frappe.db.get_value("Company", {}, "name")
    doc.update(extra)
    doc.insert()
    return doc


class TestEmployeeRules(DuplicateGuardTestBase):
    """The HR carve-outs: rejoining staff and shared official mailboxes.

    These encode business rules that look like bugs if you do not know them:
    an employee's details are only reserved while they are *active*, and an
    official company address may legitimately sit on several employees.
    """

    def test_active_employee_phone_is_unique(self):
        make_employee("Asha Rao", cell_number="9811100011")
        with self.assertRaises(DuplicateError):
            make_employee("Bela Sen", cell_number="+91 9811100011")

    def test_active_employee_name_is_unique(self):
        make_employee("Chetan Iyer")
        with self.assertRaises(DuplicateError):
            make_employee("chetan   IYER")

    def test_employee_name_check_can_be_disabled(self):
        apply_settings(check_employee_names=0)
        make_employee("Devi Nair")
        second = make_employee("Devi Nair")  # allowed: names are not checked
        self.assertTrue(second.name)

    def test_left_employee_frees_up_phone(self):
        # A resigned employee's number can be reused - including by the same
        # person rejoining under a new Employee record.
        make_employee("Esha Roy", status="Left", cell_number="9811100022")
        rejoiner = make_employee("Esha Roy", cell_number="9811100022")
        self.assertTrue(rejoiner.name)

    def test_resignation_releases_the_number(self):
        first = make_employee("Farid Khan", cell_number="9811100033")
        first.status = "Left"
        first.save()
        replacement = make_employee("Gita Bose", cell_number="9811100033")
        self.assertTrue(replacement.name)

    def test_company_email_may_be_shared(self):
        make_employee("Hari Menon", personal_email="accounts@example-corp.com")
        second = make_employee("Ila Dutt", personal_email="accounts@example-corp.com")
        self.assertTrue(second.name)

    def test_personal_email_is_still_unique(self):
        make_employee("Jai Verma", personal_email="jai@gmail.com")
        with self.assertRaises(DuplicateError):
            make_employee("Kiran Das", personal_email="JAI@Gmail.com")

    def test_employee_does_not_collide_with_sales(self):
        # HR and Sales are separate functions: the same person may be both an
        # employee and a customer contact.
        make_employee("Lata Pillai", cell_number="9811100044")
        lead = make_lead("Some Org", mobile_no="9811100044")
        self.assertTrue(lead.name)


class TestRenameKeepsIndexAccurate(DuplicateGuardTestBase):
    def test_renaming_a_customer_moves_its_index_rows(self):
        # Without the after_rename hook the old rows are orphaned: they keep
        # matching, so the next record to use the value is blocked and told it
        # belongs to a record that no longer exists.
        cust = make_customer("Rename Me Ltd")
        frappe.rename_doc("Customer", cust.name, "Renamed Ltd", force=True)

        stale = frappe.db.count(
            "Duplicate Index",
            {"reference_doctype": "Customer", "reference_name": cust.name},
        )
        self.assertEqual(stale, 0)

        moved = frappe.db.count(
            "Duplicate Index",
            {"reference_doctype": "Customer", "reference_name": "Renamed Ltd"},
        )
        self.assertGreaterEqual(moved, 1)


class TestAuditScanner(DuplicateGuardTestBase):
    def test_audit_finds_preexisting_duplicates(self):
        # Migration Mode only reports what someone re-saves; the audit finds the
        # collisions already sitting in the data.
        apply_settings(migration_mode=1)
        make_lead("Audit Org A", mobile_no="9812200011")
        make_lead("Audit Org B", mobile_no="9812200011")

        result = api.audit_duplicates()
        values = {group["value"] for group in result["duplicates"]}
        self.assertIn("+919812200011", values)

    def test_audit_ignores_same_entity_pairs(self):
        # A Contact legitimately carries its own Lead's number.
        apply_settings(migration_mode=1)
        lead = make_lead("Same Entity Org", mobile_no="9812200022")
        make_contact(phone="9812200022", link_doctype="Lead", link_name=lead.name)

        result = api.audit_duplicates()
        values = {group["value"] for group in result["duplicates"]}
        self.assertNotIn("+919812200022", values)

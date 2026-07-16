# Changelog

## 1.1.0

### Fixed — critical

- **The legacy id field was renamed inconsistently, breaking the legacy import.**
  `setup/install.py` created the field as `legacy_id` while `handlers/common.py`
  and `api.py` still looked up `legacy_yetiforce_id`. On a fresh install the
  upsert API queried a column that did not exist; the re-import protection it
  provides was therefore not working at all. The field is now `legacy_id`
  natively, everywhere. No rename patch ships: a new installation has only ever
  seen one name.

- **Index rows were not maintained while the guard was disabled.**
  `core/index.py` returned early from `sync_document` when `is_enabled()` was
  false — including the delete. Since switching the guard off is exactly what an
  administrator does to clean up messy data, every edit made while it was off
  left the index describing values that no longer existed. Switching the guard
  back on then rejected saves colliding with phantom values that appear on no
  record. Index maintenance is now unconditional; only *enforcement* is switched.

- **Renaming a record orphaned its index rows.** No `after_rename` hook existed.
  Renaming a Customer (routine in ERPNext, where the record is often named after
  `customer_name`) left rows pointing at a name that no longer existed. Those
  orphans still matched, so the next record to use that phone number was blocked
  and told it belonged to a record nobody could find — and the rows were never
  cleaned up, because `on_trash` only ever fires for the *new* name. Added
  `core/index.sync_on_rename`, wired for all five guarded DocTypes.

- **The employee email exemption was silently disabled.**
  `EMPLOYEE_EMAIL_EXEMPT_DOMAINS` had been emptied, so official company mailboxes
  were treated as personal addresses and the second employee to receive one was
  blocked. Now read from Settings (see below).

### Fixed

- Blocked saves showed **two dialogs** for one problem (`msgprint` followed by a
  bare `raise`). Now a single `frappe.throw`. The structured match list is
  available at `frappe.local.duplicate_guard_matches`.
- Value-type labels were translated **at import time**, baking in whichever
  language the worker first served for every later user. Now resolved per call.
- `hooks.py` declared `required_apps = ["frappe"]`, allowing installation onto a
  bare Frappe site where every guarded DocType is undefined. Now requires
  `erpnext`.
- `hooks.py` exported the opt-in legacy field as a **fixture**, which would push
  an unexplained "Legacy ID" field onto the Customer and Lead forms of every site
  installing the app. The fixtures block is removed.
- `rebuild_all_indexes` raised if a DocType named in the (free-text) guarded list
  did not exist on the site, aborting the whole rebuild and leaving the index
  half-built with no version stamp. Missing DocTypes are now skipped and reported.
- Migration Mode re-collected and re-normalized the entire document once per
  match when writing report rows. The already-collected values are now reused.
- `tests/test_validator.py` defined `setUpClass` twice.

### Added

- **All configuration moved into Settings** — no site needs to edit Python:
  - *Active Employee Statuses* — which statuses reserve a person's details.
  - *Company Email Domains (Exempt)* — official mailboxes employees may share.
  - *Check Employee Names* — switch off where different people share names.
  - *Phone / Email / Ignored field overrides* — these were already read by
    `core/metadata.py` but **had no fields in the DocType**, so they could never
    be set.
- Settings validation that catches mistakes which would otherwise fail silently:
  an unknown employee status (which would make every employee look inactive and
  quietly stop all employee checking), a malformed email domain, and an
  unparseable `DocType: field` override line.
- **Audit Existing Data** button on Duplicate Guard Settings; mode banner now
  distinguishes disabled / migration / strict.
- Test coverage for the HR rules (rejoining staff, shared mailboxes, personal
  email, name toggle, HR-vs-Sales isolation), for rename index accuracy, and for
  the audit scanner. 30 → 41 tests.

### Changed

- Internal index names `crm_dg_*` → `dg_*`, with the superseded names dropped on
  migrate so an upgraded site does not carry two identical indexes.
- Doc flag `crm_dg_skip_legacy_guard` → `duplicate_guard_skip_legacy_guard`.
- Documentation rewritten and made vendor-neutral throughout.

### Upgrading

`INDEX_VERSION` is now 3. **A rebuild is required** — Employee names are indexed,
inactive employees and `fetch_from` mirrors are no longer indexed, field
discovery is wider, and phone normalization is country-aware per record:

```bash
bench --site yoursite migrate
bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes
bench restart
```

`bench migrate` will warn until the rebuild is done.

**Set your company email domains** in Duplicate Guard Settings → Employee (HR)
Rules before going live, or shared mailboxes will be blocked.

---

## 1.0.0

Initial release.

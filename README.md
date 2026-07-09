# Duplicate Guard

Prevent duplicate parties and contacts in ERPNext / Frappe **v15 and v16** —
across **Sales, Purchase and HR** — with per-function scoping.

`duplicate_guard` detects and blocks duplicate records no matter how they enter
the system — the desk UI, the REST API, the Data Import tool, background jobs,
`bench execute`, Server Scripts, or any programmatic `doc.insert()`. It is built
to scale to **500,000+** records by using a pre-normalized, indexed lookup table
instead of scanning the underlying tables.

---

## What it does

- **Function-scoped dedup.** Duplicates are detected *within* a business
  function, not globally: **Sales** (Lead, Customer), **Purchase** (Supplier) and
  **HR** (Employee). The same phone/email may appear across functions (a person
  who is both a customer and a supplier) without being flagged; within one
  function it is blocked. Functions are configurable.
- **Name uniqueness** within a function. Creating a Customer named
  `ABC Industries` blocks a Lead named `ABC Industries` (both Sales), and
  Suppliers are name-checked within Purchase.
- **Normalized matching.** `ABC Industries`, `ABC   Industries` and
  `abc industries` are all treated as the same name.
- **Dynamic phone/email discovery.** Phone and email fields are found from
  DocType metadata (top-level *and* inside child tables), never hard-coded. Add a
  custom phone field tomorrow and it is covered automatically.
- **Contact-aware.** In ERPNext a Customer's / Supplier's phone/email live on
  linked **Contact** records, not on the party. This app guards Contact too, and
  a shared Contact inherits the function of whatever it links to — so two
  customers whose contacts share a phone are caught, while the same person acting
  as both a customer and supplier contact is not.
- **Country-aware phone normalization (E.164).** `+91 9876543210`,
  `09876543210` and `98765 43210` all reduce to `+919876543210`, while a US
  number `+1 9876543210` stays `+19876543210`. Uses Google's `libphonenumber`
  when installed, with a built-in fallback otherwise.
- **Email normalization.** `Sales@ABC.com` → `sales@abc.com`.
- **Within-record de-duplication.** The same number in several fields on one
  record is valid; it is not flagged against itself.
- **Lead-conversion aware.** Converting a Lead to a Customer (and the Contact
  created from it) is not flagged against the originating Lead.
- **Two modes.** *Strict Mode* rejects duplicates (production). *Migration Mode*
  logs them to a report and lets the save through (data migration / clean-up).
- **Legacy import.** A `legacy_yetiforce_id` field plus an upsert API/CSV runner
  means re-importing legacy data updates the existing record instead of
  duplicating it.
- **Address is intentionally excluded** — a firm legitimately has multiple
  addresses sharing the same contact details.

---

## Architecture at a glance

```
duplicate_guard/
└── duplicate_guard/                # the importable python package
    ├── hooks.py                        # wires everything into Frappe
    ├── api.py                          # public callables (upsert / rebuild / probe)
    ├── modules.txt, patches.txt
    │
    ├── core/                           # the reusable engine (no party specifics)
    │   ├── normalizer.py               # pure text -> canonical value functions
    │   ├── metadata.py                 # discover phone/email/name fields (incl. child tables)
    │   ├── search.py                   # collect a doc's values + query the index
    │   ├── index.py                    # maintain the Duplicate Index table
    │   ├── validator.py                # orchestrate: detect -> reject or report
    │   ├── utils.py                    # settings, function-scope + config helpers
    │   └── exceptions.py               # DuplicateError, DuplicateLegacyError, ...
    │
    ├── handlers/                       # thin per-DocType glue
    │   ├── common.py                   # legacy-id guard
    │   ├── customer.py  lead.py        # Sales
    │   ├── supplier.py                 # Purchase
    │   ├── employee.py                 # HR
    │   └── contact.py                  # shared contact details
    │
    ├── setup/install.py                # custom fields + default settings
    ├── scripts/import_legacy.py        # CSV legacy importer (upsert)
    │
    ├── duplicate_guard/doctype/        # the three DocTypes
    │   ├── duplicate_guard_settings/
    │   ├── duplicate_index/            # the fast, pre-normalized lookup table
    │   └── duplicate_report/           # Migration Mode audit trail
    │
    └── tests/                          # unit + integration tests
```

### Why it scales

Every guarded record's normalized names, phones and emails are mirrored into
`Duplicate Index`, a narrow table with a composite index on
`(scope, value_type, normalized_value)`. A duplicate check is therefore a few
index look-ups — fast and roughly constant regardless of table size — instead of
a scan over hundreds of thousands of records. Index rows are maintained
automatically on insert / update / delete, and are tagged with the business
**function scope** so matches only happen within the same function.

---

## Quick start

```bash
# 1. Get the app into your bench (from your bench folder)
bench get-app duplicate_guard /path/to/duplicate_guard

# 2. Install it on your site
bench --site yoursite install-app duplicate_guard

# 3. Build the index for data you already have (safe to re-run)
bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes
```

Full, explained, zero-to-running instructions are in
[`docs/INSTALLATION.md`](docs/INSTALLATION.md).

---

## Documentation

- [Installation Guide](docs/INSTALLATION.md) — every command explained, from an
  empty machine to a running app.
- [Configuration Guide](docs/CONFIGURATION.md) — the Settings DocType, modes,
  phone rules and scope.
- [Administrator Guide](docs/ADMINISTRATOR.md) — daily operation, legacy import,
  reviewing reports, adding new DocTypes.
- [Troubleshooting Guide](docs/TROUBLESHOOTING.md) — common problems and fixes.

---

## Running the tests

```bash
bench --site yoursite run-tests --app duplicate_guard
```

The normalizer tests are pure-Python and need no site; the validator tests create
throwaway Customers/Leads inside a rolled-back transaction.

---

## License

MIT — see [`license.txt`](license.txt).

# Duplicate Guard

Prevent duplicate parties and contacts in ERPNext / Frappe **v15 and v16** —
across **Sales, Purchase and HR** — with per-function scoping.

`duplicate_guard` detects and blocks duplicate records no matter how they enter
the system: the desk UI, the REST API, the Data Import tool, background jobs,
`bench execute`, Server Scripts, or any programmatic `doc.insert()`. It is built
to scale to **500,000+** records by using a pre-normalized, indexed lookup table
instead of scanning the underlying tables.

> **Installing on a site that already has data?** See
> [Installing on an existing site](#installing-on-an-existing-site). A strict
> duplicate guard switched on over legacy duplicates makes existing records
> uneditable — so this app detects your data and starts in a safe mode instead.

---

## What it does

- **Function-scoped dedup.** Duplicates are detected *within* a business
  function, not globally: **Sales** (Lead, Customer), **Purchase** (Supplier),
  **HR** (Employee). The same phone/email may appear across functions — a person
  who is both a customer and a supplier — without being flagged. Configurable.
- **Name uniqueness** within a function. A Customer named `ABC Industries` blocks
  a Lead named `ABC Industries` (both Sales); Suppliers are name-checked within
  Purchase.
- **Normalized matching.** `ABC Industries`, `ABC   Industries` and
  `abc industries` are the same name. `Sales@ABC.com` and `sales@abc.com` are the
  same address.
- **Country-aware phone matching (E.164).** `+91 9876543210`, `09876543210` and
  `98765 43210` all reduce to `+919876543210`, while `+1 9876543210` stays
  distinct. The region is resolved **per record** from its country field, so a UK
  lead's `07911 123456` is not read as an Indian number on an India-defaulted
  site. Uses Google's `libphonenumber` when installed, with a built-in fallback.
- **Dynamic field discovery.** Phone/email fields are found from DocType
  metadata — top-level *and* inside child tables — never hard-coded. Add a custom
  phone field tomorrow and it is covered automatically. Explicit overrides exist
  for when detection needs help.
- **Contact-aware.** In ERPNext a Customer's / Supplier's phone and email live on
  linked **Contact** records, not on the party. This app guards Contact too, and
  a Contact inherits the function of whatever it links to.
- **HR-aware.** Rejoining staff and shared official mailboxes are handled
  properly — see [Employee rules](#employee-rules).
- **It explains itself.** A blocked save names the offending value *and* the
  Customer / Lead / Supplier / Employee that already holds it, resolving a
  Contact through its links.
- **Audit scanner.** Finds the duplicates *already in your database*, without
  waiting for someone to re-save a record.
- **Two modes.** *Strict Mode* rejects duplicates (production). *Migration Mode*
  logs them and lets the save through (clean-up / imports).
- **Idempotent legacy import** (opt-in). Re-import the same export repeatedly
  without creating copies.
- **Address is intentionally excluded** — a firm legitimately has several
  addresses sharing contact details.

---

## What is checked, where

| DocType  | Function  | Name | Phone | Email | Notes |
|----------|-----------|:----:|:-----:|:-----:|-------|
| Lead     | Sales     | ✓ `company_name` | ✓ | ✓ | Organisation name only, never the person's name |
| Customer | Sales     | ✓ `customer_name` | — | — | Phone/email live on its linked Contacts |
| Supplier | Purchase  | ✓ `supplier_name` | — | — | Phone/email live on its linked Contacts |
| Employee | HR        | ✓ `employee_name` | ✓ | ✓ | Active employees only; official domains exempt |
| Contact  | inherited | — | ✓ | ✓ | Takes the function of every party it links to |
| Address  | —         | — | — | — | Deliberately not guarded |

Customer and Lead share the **Sales** scope, so they cannot hold the same name
and their Contacts cannot share a phone or email. Supplier (**Purchase**) and
Employee (**HR**) are isolated from Sales and from each other.

### Employee rules

Employees are a genuine exception to "everything must be unique", because two
real-world facts break naive dedup:

1. **People rejoin.** The same person may need a second Employee record years
   later. So name, phone and personal email are enforced **only among active
   employees** — a resigned employee's details are released for reuse. Inactive
   employees are not indexed at all.
2. **Official mailboxes are shared and reassigned.** `accounts@example.com` may
   legitimately sit on several employees and pass from a leaver to a new hire.
   Addresses on your configured company domains are **exempt** — never indexed,
   never blocked. Every *other* (personal) employee email is still enforced.

Both are configured in **Duplicate Guard Settings** → *Employee (HR) Rules*. Set
your company domains before going live, or shared mailboxes will be treated as
personal addresses and blocked.

> **Employee names.** Two genuinely different active people can share a name —
> common in many regions. Turn off **Check Employee Names** if that applies to
> you; phone and personal email are the reliable unique keys.

---

## Architecture at a glance

```
duplicate_guard/
└── duplicate_guard/                    # the importable python package
    ├── hooks.py                        # wires everything into Frappe
    ├── api.py                          # public callables (setup / audit / rebuild / upsert / probe)
    │
    ├── core/                           # the reusable engine (no party specifics)
    │   ├── normalizer.py               # pure text -> canonical value functions
    │   ├── metadata.py                 # discover phone/email/name fields (incl. child tables)
    │   ├── search.py                   # collect a doc's values + query the index
    │   ├── index.py                    # maintain the Duplicate Index table
    │   ├── validator.py                # orchestrate: detect -> reject or report
    │   ├── audit.py                    # scan for duplicates that already exist
    │   ├── utils.py                    # settings, scopes, phone region resolution
    │   └── exceptions.py               # DuplicateError, DuplicateLegacyError, ...
    │
    ├── handlers/                       # thin per-DocType glue
    │   ├── common.py                   # legacy-id guard
    │   ├── customer.py  lead.py        # Sales
    │   ├── supplier.py                 # Purchase
    │   ├── employee.py                 # HR
    │   └── contact.py                  # shared contact details
    │
    ├── setup/install.py                # data-aware defaults + opt-in custom field
    ├── scripts/import_legacy.py        # CSV importer (idempotent upsert)
    │
    ├── duplicate_guard/doctype/        # the three DocTypes
    │   ├── duplicate_guard_settings/
    │   ├── duplicate_index/            # the fast, pre-normalized lookup table
    │   └── duplicate_report/           # audit trail
    │
    └── tests/                          # unit + integration tests
```

### Why it scales

Every guarded record's normalized values are mirrored into `Duplicate Index`, a
narrow table with a composite index on
`(scope, value_type, normalized_value)`. A duplicate check is therefore a couple
of index seeks — roughly constant regardless of table size — instead of a scan
over hundreds of thousands of records. Rows are maintained automatically on
insert, update, rename and delete.

The same index makes the audit cheap: finding every pre-existing duplicate is one
`GROUP BY` over that narrow table.

---

## Quick start

### On a new / empty site

```bash
cd ~/frappe-bench
bench get-app duplicate_guard https://github.com/Cherry-Softsys-LLP/duplicate_guard
bench --site yoursite install-app duplicate_guard
bench restart
```

With no existing records there is nothing to clean up, so the app installs
straight into **Strict Mode** and starts preventing duplicates.

### Installing on an existing site

Installing a strict duplicate guard onto a database that already contains
duplicates is actively harmful. Two Customers sharing a phone number are ordinary
legacy data — but with Strict Mode on, the *first* person to edit either record
(to change a credit limit, an address, anything) has their save rejected because
of a phone number they never touched. Both records become uneditable and the
error blames a field the user was not editing. No new duplicate has been
prevented; the site is just broken.

So the app **detects existing data at install time and starts in Migration
Mode**, where collisions are recorded and nothing is blocked:

```bash
# 1. Install (auto-detects your data, enables Migration Mode, prints next steps)
bench --site yoursite install-app duplicate_guard

# 2. Build the index and audit what is already there
bench --site yoursite execute duplicate_guard.api.setup_existing_site

# 3. Review and clean up:  Desk -> Duplicate Report (Status = Open)

# 4. When the report is clear, untick Migration Mode in
#    Duplicate Guard Settings. Strict enforcement begins.
```

**Why step 2 is not optional.** Until the index is built it is *empty*, so the
guard matches nothing and appears to do no work. Rows would then trickle in as
records happened to get re-saved — and duplicates would start being blocked weeks
later, at random, on whichever record of a pair was edited second.

**Why the audit exists.** Migration Mode only writes reports from the `validate`
event, i.e. when somebody saves. A pre-existing duplicate pair nobody has touched
stays invisible, so an empty Duplicate Report does *not* mean clean data. The
audit scans the index directly and finds them all. Records that legitimately
share a value — a Contact and the party it belongs to, a Customer and the Lead it
came from, a resigned employee — are excluded.

Full instructions: [`docs/INSTALLATION.md`](docs/INSTALLATION.md).

---

## Commands

| Command | What it does |
|---|---|
| `duplicate_guard.api.setup_existing_site` | Index + audit + report. Run once after installing on a site with data. |
| `duplicate_guard.api.audit_duplicates` | Read-only scan for duplicates already in the data. `{'create_reports': True}` to log them. |
| `duplicate_guard.api.rebuild_all_indexes` | Rebuild the index for every guarded DocType, in batches. |
| `duplicate_guard.api.rebuild_index` | Rebuild one: `--kwargs "{'doctype': 'Customer'}"`. |
| `duplicate_guard.api.check_duplicates` | Read-only "would this collide?" probe. Saves nothing. |
| `duplicate_guard.core.metadata.describe` | Show which fields are detected: `--kwargs "{'doctype': 'Lead'}"`. |
| `duplicate_guard.api.mark_index_current` | Stamp the index as current after a manual rebuild. |

The first two are also buttons on **Duplicate Guard Settings**.

### Keeping the index in step with upgrades

When an app update changes *what* gets indexed, an index built by the previous
version is quietly wrong. `bench migrate` checks a version stamp and tells you
when a rebuild is needed:

```bash
bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes
```

---

## Configuration

Everything is configured from **Duplicate Guard Settings** — no code editing.
See [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

Worth setting before going live: **Default Region / Country Code** (ships as
India), and **Company Email Domains (Exempt)** if you guard Employees.

### Legacy import (opt-in)

The `legacy_id` field and its upsert API let you re-import an export repeatedly
without creating copies. Off by default, so no unexplained field appears on your
Customer and Lead forms:

```json
// sites/yoursite/site_config.json
{ "duplicate_guard_enable_legacy_id": 1 }
```

```bash
bench --site yoursite migrate
```

Workflow: [`docs/ADMINISTRATOR.md`](docs/ADMINISTRATOR.md).

---

## Documentation

- [Installation Guide](docs/INSTALLATION.md) — every command explained.
- [Configuration Guide](docs/CONFIGURATION.md) — settings, modes, scopes, HR rules.
- [Administrator Guide](docs/ADMINISTRATOR.md) — daily operation, imports, adding DocTypes.
- [Troubleshooting Guide](docs/TROUBLESHOOTING.md) — common problems and fixes.

---

## Troubleshooting quick hits

**"It let me save a duplicate."** In order: are the records in the same function
scope (two *Customers* do not collide on phone — Customer phone lives on its
Contact)? Is the index built (`setup_existing_site`)? Is Migration Mode on (it
logs instead of blocking)? Is the value on two *different* records? Did you
`bench restart`?

**"It blocks a value I cannot find."** Stale index — run `rebuild_all_indexes`.

**"Two different employees share a name and it blocks them."** Turn off *Check
Employee Names*.

**"My shared mailbox is blocked."** Set *Company Email Domains (Exempt)*.

---

## Running the tests

```bash
bench --site yoursite run-tests --app duplicate_guard
```

The normalizer tests are pure Python; the rest create throwaway records inside a
transaction that is rolled back.

---

## Contributing

Bug reports and pull requests are welcome. Please include `bench version`, your
settings, and the `Duplicate Index` rows for the value involved — that is the
single most useful diagnostic.

---

## License

MIT — see [`license.txt`](license.txt).

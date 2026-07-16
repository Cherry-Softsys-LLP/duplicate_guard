# Configuration Guide

Everything is configured from **Duplicate Guard Settings** (a Single DocType):

```
Desk → search "Duplicate Guard Settings"
```

Nothing in this app requires editing Python to configure it.

---

## Enforcement

| Field | Default | What it does |
|---|---|---|
| **Enabled** | on | Master switch. Off = no checking at all. The index keeps updating regardless, so turning it back on is always safe. |
| **Strict Mode** | on | Reject duplicates with an error. Normal production behaviour. |
| **Migration Mode** | *depends* | Do not block; log to Duplicate Report instead. **Overrides Strict Mode.** |
| **Check Names / Phone Numbers / Emails** | on | Site-wide switches per value type. |

Migration Mode defaults to **on** if the site had data when the app was
installed, and **off** on an empty site. See
[INSTALLATION.md](INSTALLATION.md#5-if-your-site-already-has-data).

> **Enabled vs Migration Mode.** "Enabled = off" means the app does nothing.
> "Migration Mode = on" means it watches and records but never blocks. Use
> Migration Mode while cleaning up; use Enabled = off only to switch the app off
> entirely.

---

## Phone normalization

Phone numbers are stored in canonical **E.164** form (`+919876543210`), so
`+91 9876543210`, `09876543210` and `98765 43210` all match — while `+1 9876543210`
stays distinct.

| Field | Default | What it does |
|---|---|---|
| **Default Country Code** | `91` | Numeric dialing code, no `+`. Used by the built-in fallback normalizer. |
| **Default Region (ISO)** | `IN` | Two-letter ISO code used to read numbers typed without a country code. |
| **National Number Length** | `10` | Expected local-number length. |

**These are only defaults.** The region is resolved **per record**: a Lead or
Supplier with a `country` set has its numbers read in that country, and a Contact
borrows the country of the party it links to. Only records with no country fall
back to the settings above. That is what stops a UK lead's `07911 123456` being
stored as an Indian number on an India-defaulted site.

> Install the `phonenumbers` package (it ships as a dependency) for
> gold-standard parsing. Without it a built-in fallback handles the common cases.

---

## Scope

| Field | Default | What it does |
|---|---|---|
| **Guarded DocTypes** | Customer, Lead, Supplier, Employee, Contact | One per line. |
| **Function Scopes** | `Sales: Lead, Customer`<br>`Purchase: Supplier`<br>`HR: Employee` | Duplicates are only detected *within* a function. |

Because Customer and Lead share **Sales**, a Customer and a Lead cannot hold the
same name, and their Contacts cannot share a phone or email. Supplier
(**Purchase**) and Employee (**HR**) are isolated: the same person may be an
employee and a supplier contact without being flagged.

A Contact has no function of its own — it inherits the scope of every party it
links to. A Contact linked to nothing falls back to a private `Contact` scope.

> **Address is deliberately not guarded.** A firm legitimately has several
> addresses sharing contact details.

---

## Employee (HR) rules

Employees are a genuine exception to "everything must be unique", because two
real-world facts break naive dedup.

| Field | Default | What it does |
|---|---|---|
| **Active Employee Statuses** | `Active` | Statuses that reserve a person's details. |
| **Company Email Domains (Exempt)** | *(blank)* | Domains whose addresses are never checked for Employees. |
| **Check Employee Names** | on | Enforce unique names among active employees. |

**People rejoin.** The same person may need a second Employee record years later.
Name, phone and personal email are enforced **only among employees in an active
status** — a resigned employee's details are released and can be reused.
Inactive employees are not indexed at all.

Add `Suspended` / `Inactive` to *Active Employee Statuses* if you want those to
keep reserving a person's details.

**Official mailboxes are shared and reassigned.** An address like
`accounts@example.com` may legitimately sit on several employees and be handed
from a leaver to a new hire. Put your company domains in *Company Email Domains
(Exempt)*, one per line:

```
example.com
example-group.com
```

Addresses on those domains are never indexed and never blocked. Every *other*
(personal) employee email is still enforced.

> **Set this before going live.** Blank means no exemption, so your shared
> mailboxes will be treated as personal addresses and the second employee to get
> one will be blocked.

**Names.** Two genuinely different active people can share a name — common in
many regions. Turn **Check Employee Names** off if that applies to you; phone and
personal email remain checked and are the reliable unique keys.

---

## Field discovery overrides

Phone, email and name fields are discovered automatically from DocType metadata —
both top-level fields and fields inside child tables (a Contact's `phone_nos` /
`email_ids` grids). Add a custom phone field tomorrow and it is covered with no
code change.

Detection uses, strongest first: the `Phone` fieldtype → an `options = "Phone"` /
`"Email"` marker → the field's name. Fields that mirror another record
(`fetch_from`) are skipped, because indexing a copy would collide a record with
its own source.

Use these only when detection gets it wrong:

| Field | Format |
|---|---|
| **Name Field Overrides** | `Lead: company_name, lead_name` |
| **Phone Field Overrides** | `Lead: mobile_no, custom_alt_number` |
| **Email Field Overrides** | `Employee: personal_email` |
| **Ignored Fields** | `Lead: email_signature` |

One DocType per line. An override **replaces** detection for that DocType and
value type; *Ignored Fields* is subtracted from whatever detection or an override
produced.

See exactly what is detected:

```bash
bench --site yoursite execute duplicate_guard.core.metadata.describe \
    --kwargs "{'doctype': 'Employee'}"
```

---

## After changing settings

Most changes take effect immediately (the caches are cleared on save).

**But if you change anything that alters what gets indexed** — guarded DocTypes,
field overrides, employee statuses or exempt domains, phone defaults — rebuild
the index so it matches the new rules:

```
Duplicate Guard Settings → Rebuild Duplicate Index
```

or:

```bash
bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes
```

Skipping this leaves the index describing the old rules: it can both miss real
duplicates and block on values that should no longer be checked.

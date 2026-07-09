# Configuration Guide

All configuration lives in one place: the **Duplicate Guard Settings**
document (search for it in the Awesomebar). It is a *Single* DocType, so there is
exactly one settings record for the whole site.

---

## Enforcement section

| Field | Meaning |
|---|---|
| **Enabled** | Master switch. When unticked, no duplicate checking happens anywhere. |
| **Strict Mode** | Reject duplicates with an error. This is the normal production behaviour. |
| **Migration Mode** | Do not block; instead log each collision to a *Duplicate Report* and allow the save. **Migration Mode overrides Strict Mode.** |
| **Check Names** | Enable/disable name-based matching. |
| **Check Phone Numbers** | Enable/disable phone-based matching. |
| **Check Emails** | Enable/disable email-based matching. |

### Choosing a mode

- **Day-to-day production:** *Enabled* on, *Strict Mode* on, *Migration Mode*
  off. New duplicates are rejected at the point of entry.
- **Importing messy legacy data:** turn *Migration Mode* on. Every collision is
  recorded in *Duplicate Report* for you to review, but imports never fail
  half-way. When the data is clean, switch back to Strict Mode.

The Settings form shows an orange banner while Migration Mode is on, and a red
banner if the guard is disabled, so the current posture is never a surprise.

---

## Phone Normalization section

Phone matching compares numbers **after** normalizing them to canonical
**E.164** international form — the country code is always part of the stored
value (e.g. `+919876543210`). This means:

- the same number entered as `+91 9876543210`, `09876543210` or `98765 43210`
  all match each other, **and**
- a number that only differs by country code (`+91 9876543210` in India vs
  `+1 9876543210` in the USA) is correctly treated as **different**, so you do
  not get false duplicates across countries.

When the `phonenumbers` package (Google's libphonenumber) is installed — it is a
declared dependency, so `bench` installs it automatically — it does the parsing,
handling every country's trunk-prefix and numbering rules. If it is ever absent,
a built-in fallback produces the same E.164 shape for the common cases.

| Field | Default | Meaning |
|---|---|---|
| **Default Country Code** | `91` | Numeric dialing code without `+`. Used by the built-in fallback when a bare number has no country code. India = `91`, USA/Canada = `1`, UK = `44`. |
| **Default Region (ISO)** | `IN` | 2-letter ISO country code used by `phonenumbers` to interpret numbers typed **without** a country code. India = `IN`, USA = `US`, UK = `GB`. |
| **National Number Length** | `10` | Expected digit count of a local number (India = `10`). Used by the fallback to detect a bare number that already embeds its country code. |

> Once you convert your contact fields to the **Phone** field type, entries carry
> an explicit `+CC` prefix, so the region/country-code defaults only ever apply
> to older bare numbers. Set *Default Region* to wherever most of your
> country-code-less legacy numbers originate.

**Important:** if you change *Default Country Code*, *Default Region* or
*National Number Length*, the way numbers normalize changes, so **rebuild the
index** afterwards (see the Administrator Guide) — otherwise old rows keep their
previous canonical form and matching is inconsistent.

---

## Scope section

| Field | Meaning |
|---|---|
| **Guarded DocTypes** | One DocType per line. Defaults to `Customer`, `Lead`, `Supplier`, `Employee`, `Contact`. Address is intentionally excluded. |
| **Function Scopes** | Business functions, one per line as `Function: DocType, DocType`. Duplicates are only detected *within* a function. Default: `Sales: Lead, Customer` / `Purchase: Supplier` / `HR: Employee`. |
| **Name Field Overrides** | Optional. Override which field(s) hold the entity name, one DocType per line, e.g. `Lead: company_name, lead_name`. Leave blank to use sensible defaults. |

### Function scoping

Duplicates are detected **within a business function**, never globally. The same
phone or email can appear in different functions (for example, a person who is
both a customer contact and a supplier contact) without being flagged; within a
single function it is blocked.

| Function | DocTypes | Where phone/email lives |
|---|---|---|
| Sales | Lead, Customer | Lead: on the record · Customer: on its Contacts |
| Purchase | Supplier | on its Contacts |
| HR | Employee | on the Employee (`cell_number`, `personal_email`, …) |

A **Contact** takes the function(s) of whatever party it links to (via its
`links` table). A Contact with no party link falls into its own `Contact` bucket.

### How coverage is split across DocTypes

ERPNext stores contact details differently per party, so the app checks
different things on each:

| DocType | Name checked | Phone/email checked | Where the phone/email lives |
|---|---|---|---|
| Lead | `company_name` | yes | directly on the Lead |
| Customer | `customer_name` | no (via Contacts) | on its linked Contacts |
| Supplier | `supplier_name` | no (via Contacts) | on its linked Contacts |
| Employee | — (people share names) | yes | directly on the Employee |
| Contact | — | yes | on the Contact (`phone_nos` / `email_ids`) |

A Customer's / Supplier's own `mobile_no` / `email_id` are read-only copies
fetched from the primary Contact, so the app deliberately does **not** check them
on the party (that would false-clash with the Contact). Instead it guards the
**Contact**, which is where the real values live.

> If you keep phone/email somewhere else (custom fields directly on a party),
> add that DocType to *Guarded DocTypes*. **Address is deliberately not guarded**
> — a firm legitimately has several addresses sharing the same contact details.

### How fields are discovered

You never list phone or email fields here — they are discovered from each
DocType's metadata automatically:

- **Phone fields**: any field of type *Phone*, or a *Data* field whose *Options*
  is `Phone`.
- **Email fields**: any *Data* field whose *Options* is `Email`.
- **Child-table fields**: phone/email fields inside child tables are also found
  (this is how a Contact's `phone_nos` / `email_ids` grids are covered).
- **Field-name fallback**: a *Data* field with no explicit *Phone*/*Email*
  option is still recognised if its name looks like a phone/email field
  (e.g. `phone`, `mobile_no`, `fax`, `whatsapp`, `email_id`). This is what lets
  the app pick up child fields such as *Contact Phone*'s `phone`, which is a
  plain Data field.

So if an administrator adds a custom `whatsapp` field to Contact or Lead, it is
included in duplicate checking immediately, with no code change. The discovery
cache is cleared automatically whenever a DocType or Custom Field is saved.

### Name fields (defaults)

| DocType | Default name field checked |
|---|---|
| Customer | `customer_name` |
| Lead | `company_name` (organization name only) |

Only the **organization name** is matched for Leads — a person's name
(`lead_name` / `first_name`) is deliberately not used, so two different
individuals who happen to share a full name are not flagged as duplicates.

If you ever want to change which field(s) count as "the name" (for example to
also match the person name on Leads, or to point at a custom field), use a
*Name Field Overrides* line, one DocType per line:

```
Lead: company_name, lead_name
Customer: customer_name
```

---

## Adding another DocType to the guard

The engine is generic. To guard, say, **Supplier** as well:

1. Add `Supplier` on its own line in *Guarded DocTypes*.
2. Add the document-event wiring for it in `hooks.py` (mirror the `Customer`
   block) and restart. This code step is required because Frappe reads event
   hooks from `hooks.py`, not from the database.
3. Run `bench execute duplicate_guard.api.rebuild_index --kwargs "{'doctype':'Supplier'}"`.

The Administrator Guide walks through this in full.

---

## After changing settings

Settings changes take effect immediately — saving the Settings document clears
the relevant caches. The one exception is a change to **Default Country Code** or
**National Number Length**, which changes how phones normalize; rebuild the index
afterwards so existing rows match the new rules.

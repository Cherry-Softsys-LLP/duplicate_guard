# Troubleshooting Guide

---

## "It let me save a duplicate"

Work through these in order — the first three explain almost every report.

**1. Are the two records in the same function scope?**

Two *Customers* with the same phone do **not** collide, and that is correct. In
ERPNext a Customer's phone/email live on its linked **Contact**, not on the
Customer, so Sales phone dedup flows through Contacts. Test with two Contacts or
two Leads. Likewise an Employee and a Supplier never collide — different
functions.

**2. Is the index built?**

A fresh install on an existing site has an **empty** index, so nothing matches:

```bash
bench --site yoursite execute duplicate_guard.api.setup_existing_site
```

**3. Is Migration Mode on?**

It logs instead of blocking — by design. Check Duplicate Guard Settings; the
banner at the top of the form tells you the current mode. If a row appeared in
**Duplicate Report** for your test, the engine is working correctly and Migration
Mode simply let it through.

**4. Is the value really on two different records?**

The same number twice on *one* record is valid and never flagged. So is a
Contact carrying its own Lead's number, or a Customer sharing a name with the
Lead it was converted from — those are the same entity wearing two hats.

**5. Is it an inactive employee?**

Deliberate: a resigned employee's details are released for reuse. See
[CONFIGURATION.md](CONFIGURATION.md#employee-hr-rules).

**6. Is it a company-domain employee email?**

Also deliberate — those are exempt.

**7. Did you restart?**

```bash
bench restart
```

Without it the workers run the old code.

---

## "It blocks a value I cannot find on any record"

The index is stale — it still describes data that has changed. Rebuild:

```bash
bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes
```

This should be rare. If it recurs, please report it.

---

## "Two different people share a name and it blocks them"

Expected for Employee name checking. Turn off **Check Employee Names** in
settings; phone and personal email keep working.

---

## "My shared mailbox is being blocked"

Set **Company Email Domains (Exempt)** in settings. Blank means no exemption.

---

## "A save is blocked but I cannot tell why"

The message names the value and the record that holds it. If it does not appear,
check the browser console and `bench --site yoursite console` logs, and confirm
you restarted after updating.

---

## "bench migrate says the index is out of date"

An app update changed what gets indexed. Run what it tells you:

```bash
bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes
```

---

## Legacy import problems

The legacy id field is **opt-in**. If `upsert_by_legacy_id` says it is not
enabled:

```json
// sites/yoursite/site_config.json
{ "duplicate_guard_enable_legacy_id": 1 }
```

```bash
bench --site yoursite migrate
```

Then confirm every source row carries a non-empty `legacy_id`, and that the CSV
header uses ERPNext **fieldnames**.

### "A Customer/Lead with Legacy ID … already exists"

Something tried a plain insert reusing a legacy id. Use the upsert API instead —
that is what it is for.

---

## Useful diagnostics

```bash
bench --site yoursite console
```

```python
# What mode am I in?
s = frappe.get_single("Duplicate Guard Settings")
print(s.enabled, s.strict_mode, s.migration_mode)

# Is the value indexed, and under which scope?
frappe.get_all("Duplicate Index",
    filters={"normalized_value": "+919876543210"},
    fields=["scope", "value_type", "reference_doctype", "reference_name"])

# What fields are being checked on a DocType?
from duplicate_guard.core.metadata import describe
describe("Employee")

# What duplicates already exist?
from duplicate_guard.api import audit_duplicates
audit_duplicates(limit=20)
```

---

## Reporting a bug

Please include: `bench version`, your Duplicate Guard Settings, the output of
`describe("<DocType>")` for the DocType involved, and the exact steps. If a save
is wrongly blocked or allowed, the `Duplicate Index` rows for the value involved
are the single most useful thing to attach.

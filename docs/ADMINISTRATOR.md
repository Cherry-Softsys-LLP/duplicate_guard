# Administrator Guide

Day-to-day operation of Duplicate Guard.

---

## The mental model

Every guarded record's normalized names, phones and emails are mirrored into a
narrow table (`Duplicate Index`), tagged with a **function scope**. A duplicate
check is a few index look-ups against that table — which is why it stays fast at
500,000+ records instead of scanning Customer or Lead.

Two things follow from this, and they explain most surprises:

1. **If it is not in the index, it cannot be matched.** A fresh install on an
   existing site has an empty index until you build it.
2. **Matching only happens within a scope.** Sales (Customer + Lead), Purchase
   (Supplier), HR (Employee). A Contact inherits the scope of whatever it links
   to.

---

## Daily operation

Nothing to do. The guard runs inside the `validate` document event, so it fires
however a record is created or changed — desk, REST API, Data Import, background
jobs, `bench execute`, Server Scripts, or a raw `doc.insert()`.

When a save is blocked the user sees which value collided and which
Customer / Lead / Supplier / Employee already holds it.

---

## Reviewing duplicates

```
Desk → Duplicate Report → filter Status = Open
```

Rows land here from two places:

- **Migration Mode**, when a save collides and is allowed through.
- **The audit scanner**, which finds collisions already in the data.

Work through them and set each to `Reviewed`, `Ignored` or `Merged`. The status
is for your team's bookkeeping; the engine does not read it.

Re-run the audit any time — it is read-only unless you ask it to write reports:

```bash
bench --site yoursite execute duplicate_guard.api.audit_duplicates
```

Or from the desk: **Duplicate Guard Settings → Audit Existing Data**.

---

## Rebuilding the index

Rebuild after: changing settings that affect what is indexed, an app update that
says the index is out of date, or any suspicion the index is stale.

```bash
# everything
bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes

# one DocType
bench --site yoursite execute duplicate_guard.api.rebuild_index \
    --kwargs "{'doctype': 'Customer'}"
```

Safe to re-run; it runs in batches and commits as it goes. On a large site run it
in a maintenance window.

---

## Checking a value without saving

```bash
bench --site yoursite execute duplicate_guard.api.check_duplicates \
    --kwargs "{'doctype': 'Lead', 'values': {'company_name': 'ABC Industries', 'mobile_no': '+91 9876543210'}}"
```

Creates nothing. Useful for dry runs and custom UIs.

---

## Importing data from a previous system

The optional **legacy id** field lets you re-import the same export repeatedly
without creating duplicates: rows are matched on `legacy_id` and **updated**
instead of inserted.

It is **opt-in**, because most sites are not migrating from anywhere and should
not carry an unexplained field:

```json
// sites/yoursite/site_config.json
{ "duplicate_guard_enable_legacy_id": 1 }
```

```bash
bench --site yoursite migrate
```

This adds a `Legacy ID` field to Customer and Lead.

| Method | Duplicate detection | Upserts on `legacy_id`? |
|---|---|---|
| Desk / REST / Data Import | yes | no — creates copies |
| `upsert_by_legacy_id` | yes | **yes** |
| `bulk_upsert_by_legacy_id` | yes | **yes**, row-isolated |
| `scripts.import_legacy.run` | yes | **yes**, streams a CSV |

**Turn on Migration Mode before a bulk import**, so collisions on name/phone/email
are logged rather than failing rows.

One record:

```bash
bench --site yoursite execute duplicate_guard.api.upsert_by_legacy_id \
  --kwargs "{'doctype': 'Lead', 'data': {'company_name': 'ABC Industries', 'legacy_id': 'LEG-1001', 'mobile_no': '+91 9876543210'}}"
```

A CSV (header row must use ERPNext fieldnames and include `legacy_id`):

```bash
bench --site yoursite execute duplicate_guard.scripts.import_legacy.run \
  --kwargs "{'doctype': 'Lead', 'file_path': 'sites/yoursite/private/files/leads.csv'}"
```

Each row is isolated with a savepoint: one bad row is rolled back and reported,
the rest of the batch still imports.

---

## Guarding another DocType

1. Add a handler in `duplicate_guard/handlers/` calling
   `validator.validate_document(doc)`.
2. Wire `validate`, `after_insert`, `on_update`, `after_rename` and `on_trash` in
   `hooks.py`, mirroring an existing block.
3. Add the DocType to **Guarded DocTypes** in settings.
4. Give it a function in **Function Scopes** (otherwise it gets a private scope
   named after itself).
5. Add it to `DEFAULT_CHECK_TYPES` in `core/utils.py` if it should not contribute
   all three value types.
6. Rebuild: `bench --site yoursite execute duplicate_guard.api.rebuild_index --kwargs "{'doctype': 'Your DocType'}"`.

---

## Turning the guard off

Untick **Enabled**. Checking stops immediately.

The index keeps being maintained while the guard is off — deliberately. That is
what makes it safe to switch off, clean up messy data, and switch back on: the
index still describes reality rather than values you deleted while it was not
looking.

---

## What is deliberately *not* blocked

Worth knowing before someone reports these as bugs:

- Two Customers with the same phone — Customer phone lives on its Contact.
- The same value across functions (customer vs supplier vs employee).
- The same value twice on one record.
- A Contact carrying its own linked party's phone/email.
- A Customer sharing a name with the Lead it was converted from.
- A resigned employee's name/phone/personal email being reused.
- A company-domain email on several employees.
- Two addresses sharing contact details — Address is not guarded.

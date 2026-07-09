# Administrator Guide

Day-to-day operation of Duplicate Guard.

---

## The moving parts

- **Duplicate Guard Settings** — the single configuration document.
- **Duplicate Index** — an internal, read-only table that mirrors every
  guarded record's normalized names/phones/emails. It is what makes checks fast.
  You never edit it by hand; the app keeps it in sync.
- **Duplicate Report** — one row per collision that was allowed through while
  Migration Mode was active.

---

## Rebuilding the index

The index fills automatically as records are saved. You only rebuild manually
when:

- you just installed the app on a site that already had data;
- you changed the **Default Country Code**, **Default Region** or **National
  Number Length** (these change how phones normalize);
- you upgraded from a pre-E.164 build of this app (see the note below);
- you upgraded from a build that did not yet guard **Contact** — existing
  Contacts must be indexed once so Customer phone/email dedup starts working:
  `bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes`;
- you suspect the index has drifted (e.g. after a bulk raw-SQL data load that
  bypassed the document lifecycle).

> **Upgrading to the country-code-aware (E.164) version.** Earlier builds stored
> phone values as bare national digits (e.g. `9876543210`). This version stores
> full international form (e.g. `+919876543210`). After deploying it you **must**
> rebuild the index once, or phone matching will be inconsistent until every
> record is re-saved:
>
> ```bash
> bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes
> ```

Rebuild everything:

```bash
bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes
```

Rebuild one DocType (with a custom batch size):

```bash
bench --site yoursite execute duplicate_guard.api.rebuild_index \
  --kwargs "{'doctype': 'Customer', 'batch_size': 5000}"
```

Rebuilds are idempotent (they clear and repopulate that DocType's rows) and
memory-safe (records are streamed in batches, never all loaded at once).

---

## Importing legacy YetiForce data

Every guarded DocType has a `legacy_yetiforce_id` field. The **supported** way to
import legacy records is to upsert on that id, which updates the existing record
when the legacy id is already present instead of creating a second copy.

### Which import method to use

| Method | Duplicate detection | Upsert on `legacy_yetiforce_id`? |
|---|---|---|
| Built-in **Data Import** tool | Yes (our hooks fire per row) | **No** — it can only update by ERPNext record id (`name`), not a custom field. Re-importing existing legacy ids will error on those rows. |
| **CSV script** below / the upsert API | Yes | **Yes** — true idempotent update-not-duplicate. |

So the built-in Data Import tool is fine for a *first* load of brand-new records
and for catching duplicates, but for anything you might re-import, or where the
same legacy id can recur, use the upsert path.

### One record (API)

```bash
bench --site yoursite execute duplicate_guard.api.upsert_by_legacy_id \
  --kwargs "{'doctype': 'Lead', 'data': {'company_name': 'ABC Industries', 'legacy_yetiforce_id': 'YF-1001', 'mobile_no': '+91 9876543210'}}"
```

### A whole CSV file (recommended for migration)

Put your CSV on the server with ERPNext fieldnames as the header row (including a
`legacy_yetiforce_id` column), then:

```bash
bench --site yoursite execute duplicate_guard.scripts.import_legacy.run \
  --kwargs "{'doctype': 'Lead', 'file_path': 'sites/yoursite/private/files/leads.csv'}"
```

It upserts every row, isolates failures per row (one bad row does not sink the
batch), skips blank cells so it never overwrites good data with empties, and
prints a `created / updated / failed` summary. Re-running the same file is safe.

### Recommended migration flow

1. Turn **Migration Mode** on in Settings.
2. Run the CSV script (or the bulk API). Collisions on *other* values
   (name/phone/email) are logged to *Duplicate Report* but never block a row.
3. Rebuild the index if you loaded anything via raw SQL:
   `bench execute duplicate_guard.api.rebuild_all_indexes`.
4. Review and resolve the reports (below).
5. Turn Migration Mode **off** (back to Strict Mode) for normal operation.

> If a plain `insert` (not the upsert path) tries to reuse an existing legacy
> id, it is blocked with a `DuplicateLegacyError` telling you to use the upsert
> helper. This is what prevents the built-in Data Import tool from silently
> creating legacy duplicates.

---

## REST API and custom / mobile apps

Records created or updated through the REST API (`POST /api/resource/Lead`,
`PUT /api/resource/Lead/<name>`, or `/api/method/...`) go through the same
document lifecycle as everything else, so **duplicate checking and index
maintenance happen automatically** — no special handling is needed in your app.

Two things to get right on the client side:

- **Send phone numbers with their country code** (e.g. `+91 9876543210`). The
  engine normalizes to E.164, so any format with a `+CC` prefix matches the same
  number entered elsewhere, and numbers from different countries stay distinct.
  A *bare* number (no `+CC`) is interpreted using the **Default Region** setting,
  which is only correct if that number really is from that region — so prefer
  sending the full international number.
- **Handle the duplicate error response.** In Strict Mode a duplicate makes the
  create/update call fail with a Frappe validation error (typically HTTP 417).
  The response body carries the message in `_server_messages` / `exception`;
  surface that to the user (our message names the value, the existing record and
  the field). For updates, send the record's `name` so the save ignores the
  record itself.

> **Not our rule, but you'll hit it:** ERPNext's own Lead validation requires a
> **Person Name** (`first_name`) *or* an organization name (`company_name` with
> the "Lead is an Organization" option). A Lead created via REST or Data Import
> without either fails with HTTP 417 **before** our duplicate check even runs, so
> make sure your payloads include one. This is standard ERPNext behaviour, not
> something this app adds.

---

## Reviewing duplicate reports

Open the **Duplicate Report** list. Each row records:

- the incoming record (*Reference DocType* / *Reference Name* / *Source Field*),
- what matched (*Duplicate Type* and *Duplicate Value*),
- the existing record it clashed with (*Matched DocType* / *Matched Name* /
  *Matched Field*),
- a **Status** you manage: `Open → Reviewed → Ignored / Merged`.

Typical workflow: filter by `Status = Open`, decide whether each pair is a true
duplicate, merge or edit the records in ERPNext, then set the report’s status to
`Merged` or `Ignored`.

---

## Reading a Strict-Mode error

When Strict Mode blocks a save, the message names exactly what clashed:

```
Duplicate Phone Number "9876543210" already exists.
Existing: Customer CUST-00045 (ABC Industries - Sitabuldi)
Conflicting field on this Lead: whatsapp
```

- the value that collided and its type,
- the existing record (with its display title in brackets),
- which field on the record you were saving produced the clash.

If several values clashed, the extra ones are listed under "Other conflicts".

---

## Adding a new guarded DocType (e.g. Supplier)

1. **Settings** → *Guarded DocTypes*: add `Supplier` on its own line.
2. **Code** → in `hooks.py`, add a block under `doc_events` mirroring `Customer`:

   ```python
   "Supplier": {
       "validate": "duplicate_guard.handlers.customer.validate_customer",
       "after_insert": "duplicate_guard.core.index.sync_document",
       "on_update": "duplicate_guard.core.index.sync_document",
       "on_trash": "duplicate_guard.core.index.delete_index_on_trash",
   },
   ```

   (You can reuse `validate_customer` since it just calls the generic validator,
   or add a `validate_supplier` wrapper for clarity.) If you also want the
   legacy-id guard, add `legacy_yetiforce_id` to Supplier and wire
   `before_insert` too.
3. If the entity name lives in a non-default field, add a *Name Field Override*:
   `Supplier: supplier_name`.
4. `bench --site yoursite migrate && bench restart`.
5. `bench --site yoursite execute duplicate_guard.api.rebuild_index --kwargs "{'doctype':'Supplier'}"`.

---

## A note on raw SQL

Duplicate checking runs inside the document lifecycle (`validate`) and the index
is maintained by document events (`after_insert` / `on_update` / `on_trash`).
Direct `frappe.db.sql("INSERT ...")` or `frappe.db.set_value` calls bypass that
lifecycle entirely — no framework can intercept them. If you load data that way,
run a full index rebuild afterwards, and be aware that such rows were never
duplicate-checked.

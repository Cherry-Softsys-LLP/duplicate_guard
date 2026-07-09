# Troubleshooting Guide

Symptoms, likely causes and fixes. Commands assume you are inside `frappe-bench`;
replace `yoursite` with your site name.

---

### Duplicates are not being blocked at all

1. **Guard disabled or in Migration Mode.** Open *Duplicate Guard Settings*.
   For blocking behaviour you need *Enabled* on, *Strict Mode* on, *Migration
   Mode* off.
2. **The DocType is not guarded.** Check *Guarded DocTypes* lists the DocType,
   and that `hooks.py` has a `doc_events` block for it. A DocType listed in
   settings but missing from `hooks.py` is never intercepted.
3. **Existing data was never indexed.** If the "existing" record predates the
   install, it may not be in `Duplicate Index`. Rebuild:
   `bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes`.
4. **Workers not restarted after a code change.** `bench restart` (or restart
   `bench start`).

---

### A save is blocked but the two records are genuinely different

- **Phone false match.** With country-code-aware normalization this is rare, but
  check *Default Region* / *Default Country Code*: if two bare (no `+`) numbers
  from different countries are both being interpreted with the same default
  region, they can collapse together. Store numbers with their `+CC` prefix
  (convert the field to the Phone type) and set the correct default region, then
  rebuild the index.
- **Phone numbers that should match no longer do (after upgrade).** If you
  upgraded to the E.164 version and did not rebuild, old rows still hold bare
  national digits while new saves hold `+CC...` form. Rebuild once:
  `bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes`.
- **Name false match.** Two entities share a normalized name (case/spacing
  removed). This is by design — decide which record should keep the name, or use
  a more specific name.
- Read the error message: it names the value, the existing record, and the field.
  That tells you exactly which value triggered it.

---

### Editing and re-saving a record raises a duplicate error against itself

This should never happen — the validator always excludes the current document. If
it does:

- Confirm you are on the latest app code (`git log` in `apps/duplicate_guard`)
  and have run `bench restart`.
- Rebuild the index to clear any stale rows:
  `bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes`.

---

### Lead conversion is blocked by the originating Lead

The validator ignores the originating Lead only when the new Customer’s
`lead_name` field points at that Lead. If your conversion flow does not set
`Customer.lead_name`, the link is missing and the Lead looks like any other
record. Ensure the conversion populates `lead_name` with the source Lead’s id.

---

### Legacy import creates duplicates instead of updating

- Make sure you are calling `duplicate_guard.api.upsert_by_legacy_id`, not a
  plain `insert` / the Data Import tool. Only the upsert helper matches on
  `legacy_yetiforce_id`.
- Confirm each source row actually carries a non-empty `legacy_yetiforce_id`.
- Confirm the `legacy_yetiforce_id` custom field exists on the DocType
  (`bench --site yoursite execute duplicate_guard.setup.install.after_migrate`
  recreates it if missing).

---

### "A Customer/Lead with Legacy YetiForce ID … already exists"

That is the legacy guard doing its job: a plain insert tried to reuse a legacy id.
Use `upsert_by_legacy_id` to update the existing record instead.

---

### Newly added custom phone/email field is not being checked

The field-map cache should clear automatically when you save a Custom Field. If
it did not:

```bash
bench --site yoursite clear-cache
bench --site yoursite execute duplicate_guard.core.metadata.clear_field_cache
```

Also confirm the field is actually a *Phone*/*Email*-typed field: a *Data* field
needs its *Options* set to `Phone` or `Email` to be discovered.

---

### Performance feels slow on a very large site

- Ensure the composite index exists. It is created by `on_doctype_update`; force
  it with `bench --site yoursite migrate`. You can verify in MariaDB:
  `SHOW INDEX FROM \`tabDuplicate Index\`;` — look for `crm_dg_type_value`.
- Ensure the index is populated (`SELECT COUNT(*) FROM \`tabDuplicate Index\`;`
  should be in the same order of magnitude as your Customers + Leads times the
  number of checked values per record).
- Rebuild with a larger batch size to speed up the back-fill:
  `--kwargs "{'doctype':'Customer','batch_size':10000}"`.

---

### Lead creation fails via REST/import even though it's not a duplicate

ERPNext requires every Lead to have a **Person Name** (`first_name`) or an
organization name (`company_name` with the organization option). A payload with
neither fails with HTTP 417 (mandatory field) *before* the duplicate check runs.
Add `first_name` (or `company_name`) to your import rows / API payloads. This is
core ERPNext behaviour, independent of this app.

---

### Tests fail to create a Customer or Lead

The integration tests need the standard ERPNext masters (root Customer Group /
Territory). Run them on a site that has ERPNext installed, not a bare Frappe site:

```bash
bench --site yoursite run-tests --app duplicate_guard
```

---

### Getting more detail

Check the site error log in the UI (**Error Log** list) or the file logs under
`frappe-bench/logs/`. When reporting an issue, include the full Strict-Mode error
message and the output of `bench version`.

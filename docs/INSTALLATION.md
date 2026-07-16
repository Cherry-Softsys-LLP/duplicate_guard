# Installation Guide

This guide assumes you have **never installed a custom ERPNext app** before. It
explains every command. Do the steps in order and wait for each to finish.

> Replace `yoursite` with your actual site name (the folder under
> `frappe-bench/sites/`, e.g. `mysite.localhost`).

**If your site already has customers, leads, suppliers, employees or contacts,
[Step 5](#5-if-your-site-already-has-data) is not optional.**

---

## 0. What you need first

A working **Frappe/ERPNext v15 or v16 bench**. ERPNext is required, not optional:
every DocType this app guards (Customer, Lead, Supplier, Employee) is defined by
ERPNext.

From inside `frappe-bench`:

```bash
bench version
```

You should see both `frappe` and `erpnext` at 15.x or 16.x.

> **Frappe v16 note.** On v16, whitelisted methods that change data
> (`rebuild_index`, `rebuild_all_indexes`, `setup_existing_site`,
> `upsert_by_legacy_id`) are POST-only, in line with v16's rule that
> state-changing endpoints reject GET. `bench execute` is unaffected.

---

## 1. Get the app into your bench

```bash
cd ~/frappe-bench
bench get-app duplicate_guard https://github.com/your-org/duplicate_guard
```

Or from a local folder:

```bash
bench get-app /path/to/duplicate_guard
```

This copies the code into `apps/duplicate_guard` and installs its Python
dependency (`phonenumbers`) into the bench environment.

---

## 2. Install it on your site

```bash
bench --site yoursite install-app duplicate_guard
```

This creates the three DocTypes (`Duplicate Guard Settings`, `Duplicate Index`,
`Duplicate Report`) and writes a default settings record.

**Read the output.** The app inspects your data during installation and tells you
which mode it chose:

- **Empty site** → installs in **Strict Mode**. It is already preventing
  duplicates. Skip to step 6.
- **Site with existing records** → installs in **Migration Mode** and prints a
  box of next steps. Nothing is blocked yet. Continue to step 5.

---

## 3. Apply schema changes

Usually automatic during `install-app`. If you later pull an update:

```bash
bench --site yoursite migrate
```

If `migrate` reports that the duplicate index is out of date, run the rebuild it
tells you to run. That message appears when an app update changed *what* gets
indexed, which makes an index built by the old version quietly wrong — it can
both miss real duplicates and block on values that are no longer indexed.

---

## 4. Restart

```bash
bench restart      # production (supervisor)
```

If you run `bench start` in a terminal, stop it with Ctrl-C and start it again.
Without this the workers keep running the old code and the app appears to do
nothing.

---

## 5. If your site already has data

Installing a strict duplicate guard onto a database that already contains
duplicates is actively harmful, so this app does not do it.

Two customers sharing a phone number are ordinary legacy data. With Strict Mode
on, the *first* person to edit either record — to change a credit limit, an
address, anything at all — has their save rejected because of a phone number they
never touched. Both records become uneditable, background jobs touching them
start failing, and the error blames a field the user was not editing. No new
duplicate has been prevented; the site is just broken.

So the app starts in **Migration Mode** on a site with data, and gives you one
command:

```bash
bench --site yoursite execute duplicate_guard.api.setup_existing_site
```

It does three things, in the only safe order:

1. **Builds the index** for every guarded DocType, in memory-safe batches.
2. **Audits** it for duplicates that already exist.
3. **Writes them to Duplicate Report** and prints a summary.

It deliberately does *not* change your mode. Going strict stays your decision.

**Why the index build matters.** Until it runs the index is empty, so the guard
matches nothing and looks broken. Rows would then trickle in as records happened
to get re-saved, and duplicates would start being blocked weeks later, at random,
on whichever record of a pair was edited second.

**Why the audit matters.** Migration Mode only writes reports from the `validate`
event — that is, when somebody saves. A pre-existing duplicate pair nobody has
touched stays invisible. An empty Duplicate Report does **not** mean clean data.
The audit scans the index directly and finds them all, with no saves required.

Then review:

```
Desk → Duplicate Report → filter Status = Open
```

Clean up (merge, correct, or mark Ignored). Re-run the audit any time — it never
blocks anything:

```bash
bench --site yoursite execute duplicate_guard.api.audit_duplicates
```

When the report is clear:

```
Desk → Duplicate Guard Settings → untick Migration Mode → Save
```

Strict enforcement begins.

---

## 6. Configure for your organisation

Open **Duplicate Guard Settings**. At minimum:

| Setting | Why |
|---|---|
| **Default Region / Default Country Code** | Ships as India (`IN` / `91`). Set to yours. |
| **Company Email Domains (Exempt)** | **Set this if you guard Employees.** Official mailboxes are shared and reassigned; without this they are treated as personal addresses and blocked. |
| **Check Employee Names** | On by default. Turn off if two different active employees may share a name. |

See [CONFIGURATION.md](CONFIGURATION.md) for everything else.

---

## 7. Verify

See what the app detected on a DocType:

```bash
bench --site yoursite execute duplicate_guard.core.metadata.describe \
    --kwargs "{'doctype': 'Lead'}"
```

Then try it for real: create two Contacts with the same phone number. The second
save should be rejected with a message naming the record that already holds it.

> **Testing tip.** Two *Customers* with the same phone will **not** collide —
> that is correct, not a bug. In ERPNext a Customer's phone lives on its linked
> **Contact**, so phone dedup for Sales flows through Contacts. Test with two
> Contacts, or two Leads.

---

## Running the tests

```bash
bench --site yoursite run-tests --app duplicate_guard
```

The normalizer tests are pure Python. The rest create throwaway records inside a
transaction that is rolled back afterwards.

---

## Uninstalling

```bash
bench --site yoursite uninstall-app duplicate_guard
```

This removes the app's DocTypes and its index. If you enabled the optional legacy
id field, that custom field and its data are **not** removed automatically —
delete it from Customize Form if you want it gone.

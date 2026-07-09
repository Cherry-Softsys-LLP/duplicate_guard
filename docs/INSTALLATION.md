# Installation Guide

This guide assumes you have **never created or installed an ERPNext app** before.
It explains every command. Do the steps in order and wait for each one to finish
before starting the next.

> Throughout, replace `yoursite` with your actual site name (the folder under
> `frappe-bench/sites/`, e.g. `mysite.localhost`).

---

## 0. What you need first

You need a working **Frappe/ERPNext v15 or v16 bench**. A "bench" is the
workspace that holds the framework, your apps and your sites. If you already run
ERPNext, you have one. If not, follow the official Frappe "Installation" docs to
create a bench and a site with ERPNext installed, then come back here.

Check your versions from inside the `frappe-bench` folder:

```bash
bench version
```

You should see `frappe 15.x.x` (or `16.x.x`) and `erpnext 15.x.x` (or `16.x.x`).

> **Frappe v16 note.** This app is compatible with both v15 and v16. On v16, the
> whitelisted API methods that change data (`rebuild_index`,
> `rebuild_all_indexes`, `upsert_by_legacy_id`) are POST-only, in line with v16's
> rule that state-changing endpoints reject GET. `bench execute` is unaffected by
> that rule; only direct HTTP/REST callers need to use POST.

---

## 1. Get the app onto your bench

An app must live inside `frappe-bench/apps/` before a site can use it. There are
two common ways to put it there.

### Option A — you have the app folder (this package)

From **inside your `frappe-bench` folder**, run:

```bash
bench get-app duplicate_guard /path/to/duplicate_guard
```

- `bench get-app` copies (or clones) an app into `apps/` and registers it with
  the bench (adds it to `sites/apps.txt` and installs its Python package into the
  bench's virtual environment).
- `duplicate_guard` is the app name.
- `/path/to/duplicate_guard` is the folder containing this project (the
  folder that has `pyproject.toml` in it).

### Option B — start a brand-new empty app instead

If you wanted to scaffold from scratch rather than use this package, the command
is:

```bash
bench new-app duplicate_guard
```

- `bench new-app` creates a fresh, empty app skeleton under `apps/` and asks you
  a few questions (title, publisher, license). It is the "File → New Project" of
  Frappe. You would then copy the files from this package over the generated
  skeleton. **If you used Option A, skip this.**

Verify the app is present:

```bash
ls apps/duplicate_guard
```

You should see `duplicate_guard/`, `pyproject.toml`, `README.md`, etc.

---

## 2. Install the app on your site

Putting the app in `apps/` is not enough; each **site** must have it installed.
Installing runs the app's database migrations (creating its DocTypes) and its
`after_install` setup (creating custom fields and default settings).

```bash
bench --site yoursite install-app duplicate_guard
```

- `--site yoursite` targets one specific site.
- `install-app duplicate_guard` creates the three DocTypes
  (`Duplicate Guard Settings`, `Duplicate Index`, `Duplicate Report`),
  adds the `legacy_yetiforce_id` custom field to Customer and Lead, and writes the
  default settings (guard enabled, Strict Mode on, India phone defaults).

If you see no errors, the app is installed.

> **Dependency note.** This app depends on the `phonenumbers` package for
> accurate, country-aware phone matching. `bench get-app` / `install-app` installs
> it automatically from the app's requirements. If you *updated* an already-present
> copy of the app in place (e.g. `git pull`), pull the new dependency with:
>
> ```bash
> bench setup requirements
> # or, targeting just this app's env:
> ./env/bin/pip install phonenumbers
> ```

---

## 3. Apply schema changes (usually automatic)

`install-app` already migrates the site. If you later pull new code, apply schema
changes with:

```bash
bench --site yoursite migrate
```

- `migrate` syncs every app's DocType definitions into the database and runs any
  pending patches. It also (re)creates the composite database index on the
  `Duplicate Index` table via the app's `on_doctype_update` hook.

---

## 4. Build the index for existing data

If your site already has Customers and Leads, they are **not** in the duplicate
index yet (the index only fills automatically for records saved *after* install).
Back-fill them once:

```bash
bench --site yoursite execute duplicate_guard.api.rebuild_all_indexes
```

- `bench execute` runs a single Python function inside the site's context.
- `duplicate_guard.api.rebuild_all_indexes` walks every guarded DocType in
  memory-safe batches (2000 records at a time by default) and populates
  `Duplicate Index`. It commits after each batch, so it is safe to interrupt
  and re-run.

To rebuild just one DocType:

```bash
bench --site yoursite execute duplicate_guard.api.rebuild_index --kwargs "{'doctype': 'Customer'}"
```

On a fresh site with no data, you can skip this step.

---

## 5. Restart and clear caches

```bash
bench --site yoursite clear-cache
bench restart
```

- `clear-cache` drops cached metadata so the new DocTypes and custom fields show
  up immediately.
- `bench restart` restarts the background workers and web server so the new
  document-event hooks are loaded. On a development bench you may instead be
  running `bench start` in a terminal — stop it with `Ctrl+C` and start it again.

---

## 6. Verify

1. In the ERP UI, search the Awesomebar for **Duplicate Guard Settings** and
   open it. You should see the settings form with *Enabled* and *Strict Mode*
   ticked.
2. Create a Customer named `Test One`. Save.
3. Create a Lead with Organization Name `Test One`. Saving should be **blocked**
   with a clear "Duplicate Name" message.

If step 3 blocks the save, the installation is working.

---

## Uninstalling

```bash
bench --site yoursite uninstall-app duplicate_guard
```

This removes the app's DocTypes and data from the site. The `legacy_yetiforce_id`
custom field is removed with the app's fixtures. The app folder stays in `apps/`
until you remove it with `bench remove-app duplicate_guard`.

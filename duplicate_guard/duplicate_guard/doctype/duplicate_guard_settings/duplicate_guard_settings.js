// Client script for Duplicate Guard Settings.
//
// Gives an administrator the two operations they actually need from the desk:
//   * "Audit Existing Data" - find the duplicates already in the database.
//   * "Rebuild Duplicate Index" - re-mirror every guarded record's values.
// It also shows a banner explaining the current mode, because "enabled but in
// Migration Mode" looks identical to "broken" if you do not know the difference.

frappe.ui.form.on("Duplicate Guard Settings", {
    refresh(frm) {
        frm.add_custom_button(__("Audit Existing Data"), () => {
            frappe.confirm(
                __("Scan the index for duplicates that already exist and log them to Duplicate Report? Nothing is blocked or changed."),
                () => {
                    frappe.call({
                        method: "duplicate_guard.api.audit_duplicates",
                        args: { create_reports: 1 },
                        freeze: true,
                        freeze_message: __("Scanning for existing duplicates..."),
                        callback: (r) => {
                            if (r.exc) return;
                            const s = r.message.summary;
                            if (!s.groups) {
                                frappe.msgprint({
                                    title: __("No Duplicates Found"),
                                    message: __("Your existing data is clean."),
                                    indicator: "green",
                                });
                                return;
                            }
                            frappe.msgprint({
                                title: __("Duplicates Found"),
                                message: __(
                                    "{0} duplicate value(s) across {1} record(s). {2} report row(s) created — <a href='/app/duplicate-report?status=Open'>review them</a>.",
                                    [s.groups, s.records, r.message.reports_created]
                                ),
                                indicator: "orange",
                            });
                        },
                    });
                }
            );
        });

        frm.add_custom_button(__("Rebuild Duplicate Index"), () => {
            frappe.confirm(
                __("Rebuild the duplicate index for all guarded DocTypes? This may take a while on large sites."),
                () => {
                    frappe.call({
                        method: "duplicate_guard.api.rebuild_all_indexes",
                        type: "POST",
                        freeze: true,
                        freeze_message: __("Rebuilding duplicate index..."),
                        callback: (r) => {
                            if (r.exc) return;
                            const rows = Object.keys(r.message || {})
                                .map((dt) => `${dt}: ${r.message[dt]}`)
                                .join("<br>");
                            frappe.msgprint({
                                title: __("Index Rebuilt"),
                                message: rows || __("Done."),
                                indicator: "green",
                            });
                        },
                    });
                }
            );
        });

        frm.dashboard.clear_headline();
        if (!frm.doc.enabled) {
            frm.dashboard.set_headline(
                __("Duplicate Guard is DISABLED: nothing is being checked."),
                "red"
            );
        } else if (frm.doc.migration_mode) {
            frm.dashboard.set_headline(
                __("Migration Mode is ON: duplicates are logged to Duplicate Report, not blocked. Run 'Audit Existing Data', clean up, then switch this off to enforce."),
                "orange"
            );
        } else if (frm.doc.strict_mode) {
            frm.dashboard.set_headline(
                __("Strict Mode: duplicates are being blocked."),
                "green"
            );
        }
    },
});

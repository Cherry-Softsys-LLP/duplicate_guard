// Client script for Duplicate Guard Settings.
//
// Adds a "Rebuild Duplicate Index" button so an administrator can (re)build the
// index for existing data straight from the UI, and shows a warning banner when
// Migration Mode is on (because duplicates are then allowed through).

frappe.ui.form.on("Duplicate Guard Settings", {
    refresh(frm) {
        frm.add_custom_button(__("Rebuild Duplicate Index"), () => {
            frappe.confirm(
                __("Rebuild the duplicate index for all guarded DocTypes? This runs in the background and may take a while on large sites."),
                () => {
                    frappe.call({
                        method: "duplicate_guard.api.rebuild_all_indexes",
                        type: "POST",
                        freeze: true,
                        freeze_message: __("Rebuilding duplicate index..."),
                        callback: (r) => {
                            if (!r.exc) {
                                frappe.msgprint({
                                    title: __("Index Rebuilt"),
                                    message: __("Done: {0}", [JSON.stringify(r.message)]),
                                    indicator: "green",
                                });
                            }
                        },
                    });
                }
            );
        });

        frm.dashboard.clear_headline();
        if (frm.doc.migration_mode) {
            frm.dashboard.set_headline(
                __("Migration Mode is ON: duplicates are logged to Duplicate Report instead of being blocked."),
                "orange"
            );
        } else if (!frm.doc.enabled) {
            frm.dashboard.set_headline(
                __("Duplicate Guard is DISABLED: no checking is happening."),
                "red"
            );
        }
    },
});

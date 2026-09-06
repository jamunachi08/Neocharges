// NeoCharges — form behaviour for all supported doctypes.

frappe.provide("neocharges");

neocharges.SUPPORTED = [
	"Quotation",
	"Sales Order",
	"Delivery Note",
	"Sales Invoice",
	"Supplier Quotation",
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
];

neocharges.config_cache = {};

neocharges.get_config = function (doctype) {
	if (neocharges.config_cache[doctype] !== undefined) {
		return Promise.resolve(neocharges.config_cache[doctype]);
	}
	return frappe
		.call({
			method: "neocharges.api.get_form_config",
			args: { doctype: doctype },
		})
		.then((r) => {
			neocharges.config_cache[doctype] = r.message || null;
			return neocharges.config_cache[doctype];
		});
};

neocharges.account_filters = function (frm) {
	const grid = frm.get_field("custom_neocharges");
	if (!grid || !grid.grid) return;

	["debit_account", "credit_account"].forEach((field) => {
		frm.set_query(field, "custom_neocharges", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 0,
			},
		}));
	});

	frm.set_query("cost_center", "custom_neocharges", () => ({
		filters: { company: frm.doc.company, is_group: 0 },
	}));
};

neocharges.apply_template = function (frm) {
	if (!frm.doc.custom_neocharge_template) return;

	frappe
		.call({
			method: "neocharges.api.get_template_lines",
			args: {
				template: frm.doc.custom_neocharge_template,
				company: frm.doc.company,
			},
		})
		.then((r) => {
			const lines = r.message || [];
			if (!lines.length) return;

			frm.clear_table("custom_neocharges");
			lines.forEach((line) => {
				const row = frm.add_child("custom_neocharges");
				Object.assign(row, line);
			});
			frm.refresh_field("custom_neocharges");
		});
};

neocharges.sync_party_requirement = function (frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const accounts = [
		["debit_account", "Debit"],
		["credit_account", "Credit"],
	];

	let pending = accounts.map(([field, side]) => {
		if (!row[field]) return Promise.resolve(null);
		return frappe
			.call({
				method: "neocharges.api.get_account_party_requirement",
				args: { account: row[field] },
			})
			.then((r) => (r.message && r.message.requires_party ? { side, ...r.message } : null));
	});

	Promise.all(pending).then((results) => {
		const hit = results.find((x) => x);
		if (hit) {
			frappe.model.set_value(cdt, cdn, "party_side", hit.side);
			if (hit.party_type && row.party_type !== hit.party_type) {
				frappe.model.set_value(cdt, cdn, "party_type", hit.party_type);
			}
		} else {
			frappe.model.set_value(cdt, cdn, "party_side", "");
			frappe.model.set_value(cdt, cdn, "party_type", "");
			frappe.model.set_value(cdt, cdn, "party", "");
		}
	});
};

neocharges.show_accrual_hint = function (frm, config) {
	if (!config || !config.is_pre_accounting) return;
	if (!frm.doc.custom_apply_neocharges) return;

	frm.dashboard.add_comment(
		__(
			"This document does not post its own GL entries. Charges entered here post an independent accrual — do not repeat them on the downstream invoice."
		),
		"yellow",
		true
	);
};

neocharges.setup = function (doctype) {
	frappe.ui.form.on(doctype, {
		onload(frm) {
			neocharges.get_config(doctype).then((config) => {
				if (!config) return;
				neocharges.account_filters(frm);

				if (frm.is_new()) {
					if (config.auto_apply && !frm.doc.custom_apply_neocharges) {
						frm.set_value("custom_apply_neocharges", 1);
					}
					if (config.default_template && !frm.doc.custom_neocharge_template) {
						frm.set_value("custom_neocharge_template", config.default_template);
					}
				}
			});
		},

		refresh(frm) {
			neocharges.get_config(doctype).then((config) => {
				if (!config) return;
				neocharges.account_filters(frm);
				neocharges.show_accrual_hint(frm, config);

				if (frm.doc.custom_neocharges_je) {
					frm.add_custom_button(
						__("Charges Journal Entry"),
						() =>
							frappe.set_route(
								"Form",
								"Journal Entry",
								frm.doc.custom_neocharges_je
							),
						__("View")
					);
				}
			});
		},

		custom_neocharge_template(frm) {
			neocharges.apply_template(frm);
		},

		custom_apply_neocharges(frm) {
			if (!frm.doc.custom_apply_neocharges) {
				frm.clear_table("custom_neocharges");
				frm.refresh_field("custom_neocharges");
			}
		},
	});
};

neocharges.SUPPORTED.forEach((doctype) => neocharges.setup(doctype));

frappe.ui.form.on("NeoCharge Line", {
	debit_account(frm, cdt, cdn) {
		neocharges.sync_party_requirement(frm, cdt, cdn);
	},
	credit_account(frm, cdt, cdn) {
		neocharges.sync_party_requirement(frm, cdt, cdn);
	},
});

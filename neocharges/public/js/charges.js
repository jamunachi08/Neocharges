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


neocharges.BASE_FIELD_BY_BASIS = {
	"Net Total": "base_net_total",
	"Grand Total": "base_grand_total",
	"Total Taxes and Charges": "base_total_taxes_and_charges",
};

// Client-side preview only. The server recomputes every percentage row on
// validate, and that value is the one that posts.
neocharges.recompute_row = function (frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (row.charge_basis !== "Percentage") return;
	if (!row.percentage || !row.percentage_on) return;

	const field = neocharges.BASE_FIELD_BY_BASIS[row.percentage_on];
	const base = flt(frm.doc[field]);
	frappe.model.set_value(cdt, cdn, "amount", flt((base * flt(row.percentage)) / 100, 2));
};

neocharges.recompute_all = function (frm) {
	(frm.doc.custom_neocharges || []).forEach((row) => {
		neocharges.recompute_row(frm, row.doctype, row.name);
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

		validate(frm) {
			neocharges.recompute_all(frm);
		},

		items_add(frm) {
			neocharges.recompute_all(frm);
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
	charge_basis(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.charge_basis === "Amount") {
			frappe.model.set_value(cdt, cdn, "percentage", 0);
			frappe.model.set_value(cdt, cdn, "percentage_on", "");
		} else {
			neocharges.recompute_row(frm, cdt, cdn);
		}
	},
	percentage(frm, cdt, cdn) {
		neocharges.recompute_row(frm, cdt, cdn);
	},
	percentage_on(frm, cdt, cdn) {
		neocharges.recompute_row(frm, cdt, cdn);
	},
});

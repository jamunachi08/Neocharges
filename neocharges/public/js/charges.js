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


// Percentage lines are computed on the document's totals, in document currency.
neocharges.DOC_FIELD_BY_BASIS = {
	"Net Total": "net_total",
	"Grand Total": "grand_total",
	"Total Taxes and Charges": "total_taxes_and_charges",
};

// ---------------------------------------------------------------------------
// Currencies. Client-side preview only: the server resolves every line's
// currency, rate and company-currency amount on validate, and that posts.
// ---------------------------------------------------------------------------
neocharges.company_currency = function (frm) {
	return (
		(frm.doc.company && erpnext.get_currency && erpnext.get_currency(frm.doc.company)) ||
		frm.doc.currency
	);
};

neocharges.document_currency = (frm) => frm.doc.currency || neocharges.company_currency(frm);

neocharges.document_rate = function (frm) {
	if (neocharges.document_currency(frm) === neocharges.company_currency(frm)) return 1;
	return flt(frm.doc.conversion_rate) || 1;
};

neocharges.posting_date = (frm) =>
	frm.doc.posting_date || frm.doc.transaction_date || frappe.datetime.get_today();

// Resolve the rate for a line; returns a promise of the rate.
neocharges.rate_for = function (frm, currency) {
	const company = neocharges.company_currency(frm);
	if (!currency || currency === company) return Promise.resolve(1);
	if (currency === neocharges.document_currency(frm)) {
		return Promise.resolve(neocharges.document_rate(frm));
	}
	return frappe
		.call({
			method: "erpnext.setup.utils.get_exchange_rate",
			args: {
				from_currency: currency,
				to_currency: company,
				transaction_date: neocharges.posting_date(frm),
			},
		})
		.then((r) => flt(r.message));
};

neocharges.update_base = function (frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const base = flt(flt(row.amount) * (flt(row.exchange_rate) || 0), 2);
	if (flt(row.base_amount) !== base) {
		frappe.model.set_value(cdt, cdn, "base_amount", base);
	}
	neocharges.update_total(frm);
};

neocharges.update_total = function (frm) {
	if (!frm.fields_dict.custom_neocharge_total) return;
	const total = (frm.doc.custom_neocharges || []).reduce((sum, row) => sum + flt(row.base_amount), 0);
	if (flt(frm.doc.custom_neocharge_total) !== flt(total, 2)) {
		frm.set_value("custom_neocharge_total", flt(total, 2));
	}
};

neocharges.set_line_currency = function (frm, cdt, cdn, currency) {
	const row = locals[cdt][cdn];
	const apply = (rate) => {
		const updates = {};
		if (row.charge_currency !== currency) updates.charge_currency = currency;
		if (rate && flt(row.exchange_rate) !== flt(rate)) updates.exchange_rate = rate;
		const done = Object.keys(updates).length
			? frappe.model.set_value(cdt, cdn, updates)
			: Promise.resolve();
		return done.then(() => neocharges.update_base(frm, cdt, cdn));
	};
	return neocharges.rate_for(frm, currency).then((rate) => {
		if (!rate) {
			frappe.show_alert({
				message: __("No exchange rate from {0} to {1}. Enter it on the line.", [
					currency,
					neocharges.company_currency(frm),
				]),
				indicator: "orange",
			});
		}
		return apply(rate);
	});
};

// The invoice's currency or rate changed: lines in document currency follow it.
neocharges.sync_document_currency_lines = function (frm) {
	const doc_currency = neocharges.document_currency(frm);
	(frm.doc.custom_neocharges || []).forEach((row) => {
		if (row.charge_basis === "Percentage") {
			neocharges.recompute_row(frm, row.doctype, row.name);
		} else if (row.charge_currency === doc_currency) {
			neocharges.set_line_currency(frm, row.doctype, row.name, doc_currency);
		}
	});
};

// Client-side preview only. The server recomputes every percentage row on
// validate, and that value is the one that posts.
neocharges.recompute_row = function (frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (row.charge_basis !== "Percentage") return;
	if (!row.percentage || !row.percentage_on) return;

	const field = neocharges.DOC_FIELD_BY_BASIS[row.percentage_on];
	const total = flt(frm.doc[field]);
	frappe.model
		.set_value(cdt, cdn, "amount", flt((total * flt(row.percentage)) / 100, 2))
		.then(() => neocharges.set_line_currency(frm, cdt, cdn, neocharges.document_currency(frm)));
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

	frm.set_query("account_head", "custom_neocharges", () => ({
		filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
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
				currency: neocharges.document_currency(frm),
				conversion_rate: frm.doc.conversion_rate,
				posting_date: neocharges.posting_date(frm),
			},
		})
		.then((r) => {
			const lines = r.message || [];
			if (!lines.length) return;

			frm.clear_table("custom_neocharges");
			lines.forEach((line) => {
				const row = frm.add_child("custom_neocharges");
				Object.assign(row, line);
				row.base_amount = flt(flt(row.amount) * flt(row.exchange_rate), 2);
			});
			frm.refresh_field("custom_neocharges");
			neocharges.update_total(frm);
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

		currency(frm) {
			neocharges.sync_document_currency_lines(frm);
		},

		conversion_rate(frm) {
			neocharges.sync_document_currency_lines(frm);
		},

		custom_neocharges_add(frm, cdt, cdn) {
			// New lines start in company currency: the common local expense.
			neocharges.set_line_currency(frm, cdt, cdn, neocharges.company_currency(frm));
		},

		custom_neocharges_remove(frm) {
			neocharges.update_total(frm);
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
	charge_currency(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.charge_basis === "Percentage" && row.charge_currency !== neocharges.document_currency(frm)) {
			frappe.show_alert({
				message: __("Percentage lines are always in the document currency."),
				indicator: "orange",
			});
			neocharges.set_line_currency(frm, cdt, cdn, neocharges.document_currency(frm));
			return;
		}
		neocharges.set_line_currency(frm, cdt, cdn, row.charge_currency || neocharges.company_currency(frm));
	},
	amount(frm, cdt, cdn) {
		neocharges.update_base(frm, cdt, cdn);
	},
	exchange_rate(frm, cdt, cdn) {
		neocharges.update_base(frm, cdt, cdn);
	},
	debit_account(frm, cdt, cdn) {
		// Choosing an account by hand overrides the document default; clearing
		// it hands the side back to the document on the next save.
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "debit_from_document", row.debit_account ? 0 : 1);
		neocharges.sync_party_requirement(frm, cdt, cdn);
	},
	credit_account(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "credit_from_document", row.credit_account ? 0 : 1);
		neocharges.sync_party_requirement(frm, cdt, cdn);
	},
	apply_to(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const native = row.apply_to && row.apply_to !== "None";

		if (native) {
			// Memo-mode fields are meaningless once ERPNext owns the posting.
			["debit_account", "credit_account", "party_type", "party", "party_side"].forEach((f) =>
				frappe.model.set_value(cdt, cdn, f, "")
			);
			if (!row.distribute_based_on) {
				frappe.model.set_value(cdt, cdn, "distribute_based_on", "Amount");
			}
		} else {
			frappe.model.set_value(cdt, cdn, "account_head", "");
			frappe.model.set_value(cdt, cdn, "distribute_based_on", "");
		}
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

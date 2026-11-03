// NeoCharges — item discounts form behaviour.
//
// Client-side preview only. The server recomputes every discounted line in
// before_validate and that value is the one that saves, posts and reaches
// the ZATCA XML. This script mirrors neocharges/discounts/calc.py so the
// form shows the right figures while typing.

frappe.provide("neocharges.discounts");

(function () {
	const ND = neocharges.discounts;

	ND.ITEM_DOCTYPES = {
		Quotation: "Quotation Item",
		"Sales Order": "Sales Order Item",
		"Delivery Note": "Delivery Note Item",
		"Sales Invoice": "Sales Invoice Item",
		"POS Invoice": "POS Invoice Item",
		"Supplier Quotation": "Supplier Quotation Item",
		"Purchase Order": "Purchase Order Item",
		"Purchase Receipt": "Purchase Receipt Item",
		"Purchase Invoice": "Purchase Invoice Item",
	};

	const F = {
		gross: "custom_nd_gross_rate",
		d1: "custom_nd_discount_1",
		d2: "custom_nd_discount_2",
		extra: "custom_nd_extra_discount",
		reason: "custom_nd_reason",
		eff: "custom_nd_effective_pct",
		unit: "custom_nd_unit_discount",
		line: "custom_nd_line_discount",
		active: "custom_nd_active",
		total: "custom_nd_total_discount",
	};
	ND.F = F;

	ND.config = {};
	ND.pending = {};
	ND.busy = {};

	ND.get_config = function (doctype) {
		if (ND.config[doctype] !== undefined) {
			return Promise.resolve(ND.config[doctype]);
		}
		if (!ND.pending[doctype]) {
			ND.pending[doctype] = frappe
				.call({
					method: "neocharges.api.get_discount_config",
					args: { doctype: doctype },
				})
				.then((r) => {
					ND.config[doctype] = r.message || null;
					return ND.config[doctype];
				});
		}
		return ND.pending[doctype];
	};

	ND.has_input = (row) => !!(flt(row[F.d1]) || flt(row[F.d2]) || flt(row[F.extra]));

	ND.compute = function (cfg, row) {
		let gross = flt(row[F.gross]) || flt(row.price_list_rate);
		if (!gross && !row[F.active]) gross = flt(row.rate);
		if (gross <= 0) return { error: __("set a Gross Rate first") };

		const p = cfg.zatca_safe ? 2 : precision("rate", row) || 2;
		const d1 = flt(row[F.d1]);
		const d2 = flt(row[F.d2]);
		const extra = flt(row[F.extra]);

		if (d1 < 0 || d1 > 100 || d2 < 0 || d2 > 100) {
			return { error: __("discount percentages must be between 0 and 100") };
		}
		if (extra < 0) return { error: __("the extra discount per unit cannot be negative") };

		gross = flt(gross, p);
		const net = flt(gross * (1 - d1 / 100) * (1 - d2 / 100) - extra, p);
		if (net < 0) return { error: __("the discounts add up to more than the gross rate") };
		if (net === 0 && !cfg.allow_full) {
			return { error: __("this discount brings the rate to zero") };
		}

		const unit = flt(gross - net, p);
		return {
			gross: gross,
			net: net,
			unit: unit,
			eff: flt((unit / gross) * 100, 3),
			line: flt(unit * flt(row.qty), precision("amount", row) || 2),
		};
	};

	ND.refresh_total = function (frm) {
		if (!frm.fields_dict[F.total]) return;
		const total = (frm.doc.items || []).reduce(
			(sum, row) => sum + (row[F.active] || ND.has_input(row) ? flt(row[F.line]) : 0),
			0
		);
		if (flt(frm.doc[F.total]) !== flt(total, 2)) {
			frm.set_value(F.total, flt(total, 2));
		}
	};

	ND.apply_row = async function (frm, cdt, cdn) {
		const cfg = ND.config[frm.doctype];
		const row = locals[cdt] && locals[cdt][cdn];
		if (!cfg || !row || ND.busy[cdn]) return;

		ND.busy[cdn] = true;
		try {
			if (!ND.has_input(row)) {
				// Inputs cleared: preview the gross rate coming back.
				if (row[F.active] && flt(row[F.gross]) > 0) {
					await frappe.model.set_value(cdt, cdn, "rate", flt(row[F.gross]));
				}
				await frappe.model.set_value(cdt, cdn, { [F.eff]: 0, [F.unit]: 0, [F.line]: 0 });
				return;
			}

			const r = ND.compute(cfg, row);
			if (r.error) {
				frappe.show_alert({ message: __("Row {0}: {1}", [row.idx, r.error]), indicator: "red" });
				return;
			}

			if (flt(row[F.gross]) !== r.gross) {
				await frappe.model.set_value(cdt, cdn, F.gross, r.gross);
			}
			if (flt(row.price_list_rate) !== r.gross) {
				await frappe.model.set_value(cdt, cdn, "price_list_rate", r.gross);
			}
			await frappe.model.set_value(cdt, cdn, "rate", r.net);
			await frappe.model.set_value(cdt, cdn, { [F.unit]: r.unit, [F.eff]: r.eff, [F.line]: r.line });

			if (!row[F.reason] && cfg.default_reason) {
				await frappe.model.set_value(cdt, cdn, F.reason, cfg.default_reason);
			}
		} finally {
			ND.busy[cdn] = false;
			ND.refresh_total(frm);
		}
	};

	ND.has_active_lines = (frm) => (frm.doc.items || []).some((row) => row[F.active]);

	ND.show_zatca_preview = function (frm) {
		frappe
			.call({
				method: "neocharges.api.zatca_discount_preview",
				args: { doctype: frm.doctype, name: frm.doc.name },
			})
			.then((r) => {
				const data = r.message;
				if (!data) return;
				const esc = frappe.utils.escape_html;
				const money = (v) => format_currency(v, data.currency);

				const rows = data.lines
					.map((line) => {
						const status = line.checks_ok
							? `<span class="indicator-pill green">${__("OK")}</span>`
							: `<span class="indicator-pill red">${esc(line.checks.join("; "))}</span>`;
						return `<tr>
							<td>${line.idx}</td>
							<td>${esc(line.item_code || "")}</td>
							<td class="text-right">${line.qty}</td>
							<td class="text-right">${money(line.base_amount)}</td>
							<td class="text-right">${money(line.allowance_amount)}</td>
							<td class="text-right">${money(line.price_amount)}</td>
							<td>${esc(line.reason_code ? line.reason_code + " — " + (line.reason || "") : "")}</td>
							<td class="text-right">${money(line.line_extension_amount)}</td>
							<td>${status}</td>
						</tr>`;
					})
					.join("");

				const flag =
					data.flag_set === null
						? __("No line-discount flag field found on this invoice.")
						: data.flag_set
						? __("Line-discount flag is ticked: the discount detail is sent to ZATCA.")
						: __("Line-discount flag is not ticked: only the net price is sent.");

				const vat = data.tax_inclusive
					? __("Prices include VAT at {0}%. Figures below are VAT-exclusive, as the XML prints them.", [
							data.vat_rate,
					  ])
					: __("Prices exclude VAT.");

				const dialog = new frappe.ui.Dialog({
					title: __("ZATCA Discount Preview"),
					size: "extra-large",
					fields: [{ fieldtype: "HTML", fieldname: "body" }],
				});
				dialog.fields_dict.body.$wrapper.html(`
					<p class="text-muted small">${esc(flag)}<br>${esc(vat)}</p>
					<div class="table-responsive">
					<table class="table table-bordered table-sm">
						<thead><tr>
							<th>#</th><th>${__("Item")}</th><th class="text-right">${__("Qty")}</th>
							<th class="text-right">${__("Base Amount")}</th>
							<th class="text-right">${__("Allowance")}</th>
							<th class="text-right">${__("Price Amount")}</th>
							<th>${__("Reason")}</th>
							<th class="text-right">${__("Line Extension")}</th>
							<th>${__("Check")}</th>
						</tr></thead>
						<tbody>${rows}</tbody>
					</table></div>`);
				dialog.show();
			});
	};

	ND.setup_parent = function (doctype) {
		frappe.ui.form.on(doctype, {
			setup(frm) {
				frm.set_query(F.reason, "items", () => ({ filters: { disabled: 0 } }));
			},

			refresh(frm) {
				ND.get_config(doctype).then((cfg) => {
					if (!cfg || !cfg.zatca_active || frm.is_new()) return;
					if (!ND.has_active_lines(frm)) return;
					frm.add_custom_button(
						__("ZATCA Discount Preview"),
						() => ND.show_zatca_preview(frm),
						__("View")
					);
				});
			},

			onload(frm) {
				ND.get_config(doctype);
			},

			items_remove(frm) {
				ND.refresh_total(frm);
			},
		});
	};

	ND.setup_items = function (item_doctype) {
		const recompute = (frm, cdt, cdn) => ND.apply_row(frm, cdt, cdn);

		frappe.ui.form.on(item_doctype, {
			[F.d1]: recompute,
			[F.d2]: recompute,
			[F.extra]: recompute,
			[F.gross]: recompute,

			price_list_rate(frm, cdt, cdn) {
				if (ND.busy[cdn] || !ND.config[frm.doctype]) return;
				const row = locals[cdt][cdn];
				if (!ND.has_input(row)) return;
				// A new item or price list brought a new price: it is the new gross.
				frappe.model.set_value(cdt, cdn, F.gross, flt(row.price_list_rate));
			},

			qty(frm, cdt, cdn) {
				const row = locals[cdt][cdn];
				if (!ND.config[frm.doctype] || !ND.has_input(row)) return;
				frappe.model
					.set_value(cdt, cdn, F.line, flt(flt(row[F.unit]) * flt(row.qty), precision("amount", row) || 2))
					.then(() => ND.refresh_total(frm));
			},
		});
	};

	Object.keys(ND.ITEM_DOCTYPES).forEach((doctype) => {
		ND.setup_parent(doctype);
		ND.setup_items(ND.ITEM_DOCTYPES[doctype]);
	});
})();

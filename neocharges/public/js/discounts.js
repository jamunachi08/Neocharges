// NeoCharges — item discounts form behaviour.
//
// Client-side preview only. The server recomputes every discounted line in
// before_validate and that value is the one that saves, posts and reaches
// the ZATCA XML. The arithmetic mirrors neocharges/discounts/calc.py.
//
// Keep Rate:   Rate and Amount are never touched. The line discount goes into
//              ERPNext's per-item discount field, and ERPNext's own totals
//              script lowers the line's net amount, VAT and the grand total.
// Reduce Rate: the discount is taken off Rate.

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
		line_amount: "custom_nd_line_amount",
		reason: "custom_nd_reason",
		eff: "custom_nd_effective_pct",
		unit: "custom_nd_unit_discount",
		net: "custom_nd_net_rate",
		line: "custom_nd_line_discount",
		active: "custom_nd_active",
		total: "custom_nd_total_discount",
		mapped: "mapped_additional_discount_amount",
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

	ND.is_keep = (frm) => !!(ND.config[frm.doctype] && ND.config[frm.doctype].mode === "keep");

	ND.has_input = (row) =>
		!!(flt(row[F.d1]) || flt(row[F.d2]) || flt(row[F.extra]) || flt(row[F.line_amount]));

	ND.source_gross = function (frm, row) {
		if (ND.is_keep(frm)) return flt(row.rate);
		return flt(row[F.gross]) || flt(row.price_list_rate) || (row[F.active] ? 0 : flt(row.rate));
	};

	ND.compute = function (frm, row) {
		const cfg = ND.config[frm.doctype];
		const keep = ND.is_keep(frm);
		let gross = ND.source_gross(frm, row);
		if (gross <= 0) return { error: __("enter a rate first") };

		const rp = keep ? precision("rate", row) || 2 : cfg.zatca_safe ? 2 : precision("rate", row) || 2;
		const ap = precision("amount", row) || 2;
		const d1 = flt(row[F.d1]);
		const d2 = flt(row[F.d2]);
		const extra = flt(row[F.extra]);
		const line_amount = flt(row[F.line_amount]);
		const qty = flt(row.qty);
		const units = Math.abs(qty);

		if (d1 < 0 || d1 > 100 || d2 < 0 || d2 > 100) {
			return { error: __("discount percentages must be between 0 and 100") };
		}
		if (extra < 0 || line_amount < 0) return { error: __("discount amounts cannot be negative") };
		if (line_amount && !units) return { error: __("a line discount amount needs a quantity") };

		gross = flt(gross, rp);
		const after = gross * (1 - d1 / 100) * (1 - d2 / 100) - extra;
		const sign = qty < 0 ? -1 : 1;

		if (keep) {
			if (after < 0) return { error: __("the discounts add up to more than the line value") };
			const line_gross = flt(gross * units, ap);
			const line_abs = flt((gross - after) * units + line_amount, ap);
			if (line_abs > line_gross) return { error: __("the discounts add up to more than the line value") };
			if (units && line_abs === line_gross && !cfg.allow_full) {
				return { error: __("this discount brings the line to zero") };
			}
			const unit = units ? line_abs / units : gross - after;
			return {
				gross: gross,
				net: flt(gross - unit, rp),
				unit: flt(unit, rp),
				eff: flt((unit / gross) * 100, 3),
				line: sign * line_abs,
				line_abs: line_abs,
			};
		}

		const net = flt(after - (units ? line_amount / units : 0), rp);
		if (net < 0) return { error: __("the discounts add up to more than the gross rate") };
		if (net === 0 && !cfg.allow_full) return { error: __("this discount brings the rate to zero") };
		const unit = flt(gross - net, rp);
		return {
			gross: gross,
			net: net,
			unit: unit,
			eff: flt((unit / gross) * 100, 3),
			line: flt(unit * qty, ap),
		};
	};

	ND.stock_qty = (row) => flt(row.qty) * (flt(row.conversion_factor) || 1);

	ND.recalculate = function (frm) {
		if (frm.cscript && frm.cscript.calculate_taxes_and_totals) {
			frm.cscript.calculate_taxes_and_totals();
		}
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

	// Fill Gross Rate and Net Rate from the row before any discount is typed.
	ND.fill_gross = function (frm, row, silent) {
		if (!row || ND.has_input(row)) return;
		const gross = ND.is_keep(frm) ? flt(row.rate) : flt(row.price_list_rate) || flt(row.rate);
		if (!gross) return;
		if (flt(row[F.gross]) === gross && flt(row[F.net]) === gross) return;
		if (silent) {
			// On load: show the value without marking the form dirty.
			row[F.gross] = gross;
			row[F.net] = gross;
		} else {
			frappe.model.set_value(row.doctype, row.name, { [F.gross]: gross, [F.net]: gross });
		}
	};

	ND.apply_row = async function (frm, cdt, cdn) {
		const cfg = ND.config[frm.doctype];
		const row = locals[cdt] && locals[cdt][cdn];
		if (!cfg || !row || ND.busy[cdn]) return;
		const keep = ND.is_keep(frm);

		ND.busy[cdn] = true;
		try {
			if (!ND.has_input(row)) {
				if (keep) {
					if (flt(row[F.mapped])) {
						row[F.mapped] = 0;
						if (!(frm.doc.items || []).some((r) => flt(r[F.mapped]))) {
							frm.doc.discount_amount = 0;
						}
					}
				} else if (row[F.active] && flt(row[F.gross]) > 0) {
					await frappe.model.set_value(cdt, cdn, "rate", flt(row[F.gross]));
				}
				await frappe.model.set_value(cdt, cdn, { [F.eff]: 0, [F.unit]: 0, [F.line]: 0 });
				ND.fill_gross(frm, row);
				if (keep) ND.recalculate(frm);
				return;
			}

			const r = ND.compute(frm, row);
			if (r.error) {
				frappe.show_alert({ message: __("Row {0}: {1}", [row.idx, r.error]), indicator: "red" });
				return;
			}

			if (keep) {
				if (flt(frm.doc.additional_discount_percentage)) {
					frappe.show_alert({
						message: __(
							"Item discounts replace the Additional Discount on this document. Clear the Additional Discount to keep both from conflicting."
						),
						indicator: "orange",
					});
				}
				const sq = Math.abs(ND.stock_qty(row));
				row[F.mapped] = sq ? flt(r.line_abs / sq, 9) : 0;
				await frappe.model.set_value(cdt, cdn, {
					[F.gross]: r.gross,
					[F.net]: r.net,
					[F.unit]: r.unit,
					[F.eff]: r.eff,
					[F.line]: r.line,
				});
				ND.recalculate(frm);
			} else {
				if (flt(row[F.gross]) !== r.gross) {
					await frappe.model.set_value(cdt, cdn, F.gross, r.gross);
				}
				if (flt(row.price_list_rate) !== r.gross) {
					await frappe.model.set_value(cdt, cdn, "price_list_rate", r.gross);
				}
				await frappe.model.set_value(cdt, cdn, "rate", r.net);
				await frappe.model.set_value(cdt, cdn, {
					[F.net]: r.net,
					[F.unit]: r.unit,
					[F.eff]: r.eff,
					[F.line]: r.line,
				});
			}

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
							<td class="text-right">${money(line.line_extension_amount)}</td>
							<td class="text-right">${money(line.line_item_discount || 0)}</td>
							<td>${esc(line.reason_code ? line.reason_code + " — " + (line.reason || "") : "")}</td>
							<td>${status}</td>
						</tr>`;
					})
					.join("");

				let doc_allowance = "";
				if (data.document_allowance) {
					const da = data.document_allowance;
					const ok = da.checks_ok
						? `<span class="indicator-pill green">${__("OK")}</span>`
						: `<span class="indicator-pill red">${__("does not equal the sum of item discounts")}</span>`;
					doc_allowance = `<p><b>${__("Document-level allowance")}:</b> ${money(da.amount)}
						${da.reason_code ? "— " + esc(da.reason_code + " " + (da.reason || "")) : ""} ${ok}</p>`;
				}

				const flag =
					data.flag_set === null
						? __("No line-discount flag field found on this invoice.")
						: data.flag_set
						? __("Line-discount flag is ticked: price-level discounts are sent to ZATCA.")
						: __("Line-discount flag is not ticked: lines are sent at their price.");

				const vat = data.tax_inclusive
					? __("Prices include VAT at {0}%. Figures are VAT-exclusive, as the XML prints them.", [
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
							<th class="text-right">${__("Price Allowance")}</th>
							<th class="text-right">${__("Price Amount")}</th>
							<th class="text-right">${__("Line Extension")}</th>
							<th class="text-right">${__("Item Discount")}</th>
							<th>${__("Reason")}</th>
							<th>${__("Check")}</th>
						</tr></thead>
						<tbody>${rows}</tbody>
					</table></div>
					${doc_allowance}`);
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
					if (!cfg) return;

					const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
					if (grid && grid.update_docfield_property) {
						// Keep Rate: the gross is the row's rate, so it is not edited here.
						grid.update_docfield_property(F.gross, "read_only", ND.is_keep(frm) ? 1 : 0);
					}

					if (frm.doc.docstatus === 0) {
						(frm.doc.items || []).forEach((row) => ND.fill_gross(frm, row, true));
					}

					if (!cfg.zatca_active || frm.is_new() || !ND.has_active_lines(frm)) return;
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

		const on_line_change = (frm, cdt, cdn) => {
			if (!ND.config[frm.doctype] || ND.busy[cdn]) return;
			const row = locals[cdt][cdn];
			if (ND.has_input(row)) {
				ND.apply_row(frm, cdt, cdn);
			} else {
				ND.fill_gross(frm, row);
			}
		};

		frappe.ui.form.on(item_doctype, {
			[F.d1]: recompute,
			[F.d2]: recompute,
			[F.extra]: recompute,
			[F.line_amount]: recompute,
			[F.gross]: (frm, cdt, cdn) => {
				if (!ND.is_keep(frm) && ND.has_input(locals[cdt][cdn])) recompute(frm, cdt, cdn);
			},

			rate(frm, cdt, cdn) {
				// Keep Rate: a new rate is a new gross. Reduce Rate sets the rate
				// itself, so only an untouched row needs its gross refreshed.
				if (ND.is_keep(frm)) {
					on_line_change(frm, cdt, cdn);
				} else if (!ND.busy[cdn] && !ND.has_input(locals[cdt][cdn])) {
					ND.fill_gross(frm, locals[cdt][cdn]);
				}
			},

			price_list_rate(frm, cdt, cdn) {
				if (ND.is_keep(frm) || ND.busy[cdn] || !ND.config[frm.doctype]) return;
				const row = locals[cdt][cdn];
				if (!ND.has_input(row)) {
					ND.fill_gross(frm, row);
					return;
				}
				// A new item or price list brought a new price: it is the new gross.
				frappe.model.set_value(cdt, cdn, F.gross, flt(row.price_list_rate));
			},

			qty: on_line_change,
			conversion_factor: on_line_change,
		});
	};

	Object.keys(ND.ITEM_DOCTYPES).forEach((doctype) => {
		ND.setup_parent(doctype);
		ND.setup_items(ND.ITEM_DOCTYPES[doctype]);
	});
})();

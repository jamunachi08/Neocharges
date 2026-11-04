// NeoCharges — ZATCA pre-submit check on draft Sales and POS Invoices.

frappe.provide("neocharges.zatca_check");

(function () {
	const ZC = neocharges.zatca_check;
	const STATUS_FIELD = "custom_nd_zatca_check";
	const COLORS = {
		Passed: "green",
		"Passed with Warnings": "orange",
		Failed: "red",
	};
	const LEVEL = {
		error: { label: __("Error"), color: "red" },
		warning: { label: __("Warning"), color: "orange" },
		info: { label: __("Info"), color: "blue" },
	};

	ZC.config = undefined;
	ZC.get_config = function () {
		if (ZC.config !== undefined) return Promise.resolve(ZC.config);
		return frappe.call({ method: "neocharges.api.get_zatca_check_config" }).then((r) => {
			ZC.config = r.message || null;
			return ZC.config;
		});
	};

	ZC.run = async function (frm) {
		if (frm.is_dirty()) {
			await frm.save();
		}
		const r = await frappe.call({
			method: "neocharges.api.run_zatca_check",
			args: { doctype: frm.doctype, name: frm.doc.name },
			freeze: true,
			freeze_message: __("Checking against ZATCA rules..."),
		});
		if (!r.message) return;
		frm.doc[STATUS_FIELD] = r.message.status;
		ZC.show_indicator(frm);
		ZC.show_result(r.message);
	};

	ZC.show_indicator = function (frm) {
		const status = frm.doc[STATUS_FIELD];
		if (!status || !COLORS[status]) return;
		frm.dashboard.add_indicator(__("ZATCA Check: {0}", [__(status)]), COLORS[status]);
	};

	ZC.show_result = function (result) {
		const esc = frappe.utils.escape_html;
		const rows = result.findings
			.map((f) => {
				const level = LEVEL[f.level] || LEVEL.info;
				return `<tr>
					<td><span class="indicator-pill ${level.color}">${level.label}</span></td>
					<td>${esc(f.area)}</td>
					<td>${esc(f.message)}${f.hint ? `<br><small class="text-muted">${esc(f.hint)}</small>` : ""}</td>
					<td class="text-muted small">${esc(__(f.source))}</td>
				</tr>`;
			})
			.join("");

		const headline =
			result.status === "Passed"
				? __("No problems found. The invoice is ready to submit.")
				: result.status === "Failed"
				? __("{0} error(s) would stop this invoice at ZATCA. Fix them before submitting.", [
						result.errors,
				  ])
				: __("{0} warning(s). The invoice can be submitted; review them first.", [result.warnings]);

		const dialog = new frappe.ui.Dialog({
			title: __("ZATCA Check"),
			size: "extra-large",
			fields: [{ fieldtype: "HTML", fieldname: "body" }],
		});
		dialog.fields_dict.body.$wrapper.html(`
			<p>
				<span class="indicator-pill ${COLORS[result.status] || "gray"}">${esc(__(result.status))}</span>
				<span class="text-muted small" style="margin-left: 8px">${esc(__("Vendor"))}: ${esc(
			result.vendor_label
		)}</span>
			</p>
			<p>${esc(headline)}</p>
			${
				rows
					? `<div class="table-responsive"><table class="table table-bordered table-sm">
						<thead><tr>
							<th style="width: 90px">${__("Level")}</th>
							<th style="width: 110px">${__("Area")}</th>
							<th>${__("Finding")}</th>
							<th style="width: 100px">${__("Rule")}</th>
						</tr></thead>
						<tbody>${rows}</tbody>
					</table></div>`
					: ""
			}`);
		dialog.show();
	};

	["Sales Invoice", "POS Invoice"].forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				ZC.get_config().then((cfg) => {
					if (!cfg) return;
					if (frm.doc.docstatus === 0) {
						frm.add_custom_button(__("ZATCA Check"), () => ZC.run(frm), __("View"));
					}
					ZC.show_indicator(frm);
				});
			},
		});
	});
})();

import frappe
from frappe import _
from frappe.model.document import Document

from neocharges.charges.custom_fields import remove_custom_fields, sync_custom_fields
from neocharges.constants import SETTINGS_DOCTYPE, SUPPORTED_DOCTYPES


class NeoChargesSettings(Document):
	def validate(self):
		seen = set()
		for row in self.doctype_configs:
			if row.document_type not in SUPPORTED_DOCTYPES:
				frappe.throw(
					_("Row {0}: {1} is not a supported document type. Supported: {2}").format(
						row.idx, row.document_type, ", ".join(SUPPORTED_DOCTYPES)
					)
				)
			if row.document_type in seen:
				frappe.throw(_("Row {0}: {1} is listed more than once").format(row.idx, row.document_type))
			seen.add(row.document_type)

			if row.default_template and row.enabled:
				self._validate_template(row)

		self._previously_enabled = frappe.get_all(
			"NeoCharge DocType Config",
			filters={"parent": SETTINGS_DOCTYPE, "enabled": 1},
			pluck="document_type",
		)

	def _validate_template(self, row):
		template = frappe.get_cached_doc("NeoCharge Template", row.default_template)
		if template.disabled:
			frappe.throw(_("Row {0}: template {1} is disabled").format(row.idx, template.name))

		is_sales = row.document_type in ("Quotation", "Sales Order", "Delivery Note", "Sales Invoice")
		wanted = "Sales" if is_sales else "Purchase"
		if template.applicable_for not in ("Both", wanted):
			frappe.throw(
				_("Row {0}: template {1} is marked for {2} and cannot be used on {3}").format(
					row.idx, template.name, _(template.applicable_for), _(row.document_type)
				)
			)

	def on_update(self):
		enabled_now = {row.document_type for row in self.doctype_configs if row.enabled}
		previously = set(getattr(self, "_previously_enabled", []) or [])

		sync_custom_fields(list(enabled_now))

		turned_off = previously - enabled_now
		if turned_off:
			remove_custom_fields(list(turned_off))

		frappe.clear_cache(doctype=SETTINGS_DOCTYPE)

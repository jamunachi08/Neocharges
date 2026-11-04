import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from neocharges.charges.custom_fields import remove_custom_fields, sync_custom_fields
from neocharges.constants import (
	DISCOUNT_CONFIG_DOCTYPE,
	DISCOUNT_DOCTYPES,
	GL_POSTING_DOCTYPES,
	METHOD_KEEP,
	POSTING_MODE_INLINE,
	SALES_DOCTYPES,
	SETTINGS_DOCTYPE,
	SUPPORTED_DOCTYPES,
)
from neocharges.discounts.custom_fields import remove_discount_fields, sync_discount_fields


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

			if row.enabled and row.posting_mode == POSTING_MODE_INLINE:
				self._validate_inline_mode(row)

			if row.default_template and row.enabled:
				self._validate_template(row)

		self._validate_discount_method()
		self._validate_discount_doctypes()
		self._validate_authorities()
		self._validate_zatca_fields()

		self._previously_enabled = frappe.get_all(
			"NeoCharge DocType Config",
			filters={"parent": SETTINGS_DOCTYPE, "enabled": 1},
			pluck="document_type",
		)
		self._previously_discount_enabled = self._stored_discount_doctypes()

	def _stored_discount_doctypes(self):
		if not frappe.db.exists("DocType", DISCOUNT_CONFIG_DOCTYPE):
			return []
		if not cint(frappe.db.get_single_value(SETTINGS_DOCTYPE, "enable_item_discounts")):
			return []
		return frappe.get_all(
			DISCOUNT_CONFIG_DOCTYPE,
			filters={"parent": SETTINGS_DOCTYPE, "enabled": 1},
			pluck="document_type",
		)

	def _validate_inline_mode(self, row):
		if row.document_type not in GL_POSTING_DOCTYPES:
			frappe.throw(
				_("Row {0}: {1} does not post its own ledger entries, so inline posting "
				  "is not possible. Use Journal Entry mode.").format(row.idx, _(row.document_type))
			)

	def _validate_template(self, row):
		template = frappe.get_cached_doc("NeoCharge Template", row.default_template)
		if template.disabled:
			frappe.throw(_("Row {0}: template {1} is disabled").format(row.idx, template.name))

		is_sales = row.document_type in SALES_DOCTYPES
		wanted = "Sales" if is_sales else "Purchase"
		if template.applicable_for not in ("Both", wanted):
			frappe.throw(
				_("Row {0}: template {1} is marked for {2} and cannot be used on {3}").format(
					row.idx, template.name, _(template.applicable_for), _(row.document_type)
				)
			)

	def _validate_discount_method(self):
		if self.get("discount_method") != METHOD_KEEP or not cint(self.get("enable_item_discounts")):
			return
		from neocharges.discounts.engine import keep_rate_supported

		if not keep_rate_supported():
			frappe.throw(
				_(
					"'Keep Rate' needs ERPNext's per-item discount, available from ERPNext v16. "
					"This site's ERPNext does not have it. Use 'Reduce Rate'."
				)
			)

	def _validate_discount_doctypes(self):
		seen = set()
		for row in self.get("discount_doctypes") or []:
			if row.document_type not in DISCOUNT_DOCTYPES:
				frappe.throw(
					_("Item discount row {0}: {1} is not supported. Supported: {2}").format(
						row.idx, row.document_type, ", ".join(DISCOUNT_DOCTYPES)
					)
				)
			if row.document_type in seen:
				frappe.throw(
					_("Item discount row {0}: {1} is listed more than once").format(row.idx, row.document_type)
				)
			seen.add(row.document_type)

	def _validate_authorities(self):
		seen = set()
		for row in self.get("discount_authorities") or []:
			if row.role in seen:
				frappe.throw(_("Authority row {0}: role {1} is listed more than once").format(row.idx, row.role))
			seen.add(row.role)
			if flt(row.max_discount_pct) < 0 or flt(row.max_discount_pct) > 100:
				frappe.throw(_("Authority row {0}: limit must be between 0 and 100").format(row.idx))

	def _validate_zatca_fields(self):
		for fieldname in ("zatca_flag_field", "zatca_reason_code_field", "zatca_reason_field"):
			value = (self.get(fieldname) or "").strip()
			if value and not value.replace("_", "").isalnum():
				frappe.throw(_("{0}: '{1}' is not a valid field name").format(self.meta.get_label(fieldname), value))
			self.set(fieldname, value)

	def on_update(self):
		enabled_now = {row.document_type for row in self.doctype_configs if row.enabled}
		previously = set(getattr(self, "_previously_enabled", []) or [])

		sync_custom_fields(list(enabled_now))

		turned_off = previously - enabled_now
		if turned_off:
			remove_custom_fields(list(turned_off))

		discount_now = set()
		if cint(self.get("enable_item_discounts")):
			discount_now = {row.document_type for row in self.get("discount_doctypes") or [] if row.enabled}
		discount_before = set(getattr(self, "_previously_discount_enabled", []) or [])

		sync_discount_fields(list(discount_now))
		if cint(self.get("zatca_check_enabled")):
			from neocharges.zatca_check.custom_fields import sync_check_fields

			sync_check_fields()
		discount_off = discount_before - discount_now
		if discount_off:
			remove_discount_fields(list(discount_off))

		frappe.clear_cache(doctype=SETTINGS_DOCTYPE)

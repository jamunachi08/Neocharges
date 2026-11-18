import frappe


def execute():
	"""v2.5: VAT on charge lines and the Invoice Line mode.

	Existing lines and template lines become 'No VAT' with net = gross =
	amount, which is exactly what they posted. The invoice-line marker field
	is added to the selling item tables.
	"""
	if not frappe.db.exists("DocType", "NeoCharge Line"):
		return

	for doctype in ("neocharge_vat_account", "neocharge_line", "neocharge_template_line", "neocharges_settings"):
		frappe.reload_doc("charges", "doctype", doctype)

	frappe.db.sql(
		"""update `tabNeoCharge Line`
		set vat_treatment = 'No VAT', vat_rate = 0,
			net_amount = amount, vat_amount = 0, gross_amount = amount,
			base_net_amount = base_amount, base_vat_amount = 0
		where ifnull(vat_treatment, '') = ''"""
	)
	frappe.db.sql(
		"""update `tabNeoCharge Template Line`
		set vat_treatment = 'No VAT', vat_rate = 0
		where ifnull(vat_treatment, '') = ''"""
	)

	if frappe.db.get_single_value("NeoCharges Settings", "default_vat_rate") in (None, 0):
		frappe.db.set_single_value("NeoCharges Settings", "default_vat_rate", 15)

	from neocharges.charges.custom_fields import sync_custom_fields

	sync_custom_fields()

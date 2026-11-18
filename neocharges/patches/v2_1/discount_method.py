import frappe


def execute():
	"""v2.1: add the Discount Method setting and the new item fields.

	Sites on ERPNext v16 move to Keep Rate (Rate and Amount unchanged).
	Lines already discounted under 2.0 switch over on their next save while
	still in draft; submitted documents are not touched.
	"""
	if not frappe.db.exists("DocType", "NeoCharges Settings"):
		return
	from neocharges.discounts.custom_fields import sync_discount_fields
	from neocharges.install import default_discount_method

	frappe.reload_doc("charges", "doctype", "neocharges_settings")
	settings = frappe.get_single("NeoCharges Settings")
	if not settings.get("discount_method"):
		settings.discount_method = default_discount_method()
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)
	else:
		sync_discount_fields()

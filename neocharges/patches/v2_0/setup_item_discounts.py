import frappe


def execute():
	"""v2.0 upgrade: seed discount reasons, switch item discounts on, add fields.

	Runs once on sites upgrading from 1.x. Fresh installs get the same from
	after_install (Frappe marks patches as done on install without running them).
	"""
	if not frappe.db.exists("DocType", "NeoCharges Settings"):
		return
	from neocharges.install import setup_item_discounts

	frappe.reload_doc("discounts", "doctype", "neodiscount_reason_account")
	frappe.reload_doc("discounts", "doctype", "neodiscount_reason")
	frappe.reload_doc("discounts", "doctype", "neodiscount_doctype_config")
	frappe.reload_doc("discounts", "doctype", "neodiscount_authority")
	frappe.reload_doc("charges", "doctype", "neocharges_settings")
	setup_item_discounts(first_run=True)

import frappe


def execute():
	"""v2.2: ZATCA vendor setting and pre-submit check (on, blocking on errors)."""
	if not frappe.db.exists("DocType", "NeoCharges Settings"):
		return
	from neocharges.install import setup_zatca_check

	frappe.reload_doc("charges", "doctype", "neocharges_settings")
	setup_zatca_check(first_run=True)

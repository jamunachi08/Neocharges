import frappe

from neocharges.charges.custom_fields import sync_custom_fields


def execute():
	"""Re-sync charge custom fields for every enabled document type."""
	if not frappe.db.exists("DocType", "NeoCharges Settings"):
		return
	sync_custom_fields()

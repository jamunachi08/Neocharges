"""Fields recording the last ZATCA check on Sales and POS Invoices."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from neocharges.constants import ZATCA_DOCTYPES
from neocharges.zatca_check.checks import CHECKED_ON_FIELD, STATUS_FIELD, STATUSES


def check_fields():
	return [
		{
			"fieldname": STATUS_FIELD,
			"label": "ZATCA Check",
			"fieldtype": "Select",
			"options": "\n".join(STATUSES),
			"default": STATUSES[0],
			"insert_after": "naming_series",
			"read_only": 1,
			"hidden": 1,
			"no_copy": 1,
			"print_hide": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": CHECKED_ON_FIELD,
			"label": "ZATCA Checked On",
			"fieldtype": "Datetime",
			"insert_after": STATUS_FIELD,
			"read_only": 1,
			"hidden": 1,
			"no_copy": 1,
			"print_hide": 1,
			"allow_on_submit": 1,
		},
	]


def sync_check_fields():
	spec = {dt: check_fields() for dt in ZATCA_DOCTYPES if frappe.db.exists("DocType", dt)}
	if spec:
		create_custom_fields(spec, ignore_validate=True)
		frappe.clear_cache()

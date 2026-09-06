"""Idempotent custom field seeding for NeoCharges.

Safe to run repeatedly: `create_custom_fields` updates in place rather than
duplicating, so this runs on install, on migrate, and whenever the Settings
document is saved with a new document type enabled.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from neocharges.constants import (
	APPLY_FIELD,
	CHARGE_ROW_DOCTYPE,
	CHARGE_TABLE_FIELD,
	JE_REFERENCE_FIELD,
	SETTINGS_DOCTYPE,
	SUPPORTED_DOCTYPES,
	TEMPLATE_DOCTYPE,
	TEMPLATE_FIELD,
)

# `items` exists on every supported sales and purchase document, which makes it
# a reliable anchor across all of them.
DEFAULT_ANCHOR = "items"

ANCHOR_OVERRIDES = {}


def transaction_fields(doctype):
	anchor = ANCHOR_OVERRIDES.get(doctype, DEFAULT_ANCHOR)
	return [
		{
			"fieldname": "custom_neocharges_section",
			"label": "Additional Charges",
			"fieldtype": "Section Break",
			"insert_after": anchor,
			"collapsible": 1,
			"collapsible_depends_on": f"eval:doc.{APPLY_FIELD}",
		},
		{
			"fieldname": APPLY_FIELD,
			"label": "Apply Additional Charges",
			"fieldtype": "Check",
			"insert_after": "custom_neocharges_section",
			"description": (
				"Post debit/credit charge lines as an independent Journal Entry. "
				"Does not affect this document's totals."
			),
		},
		{
			"fieldname": TEMPLATE_FIELD,
			"label": "Charge Template",
			"fieldtype": "Link",
			"options": TEMPLATE_DOCTYPE,
			"insert_after": APPLY_FIELD,
			"depends_on": f"eval:doc.{APPLY_FIELD}",
		},
		{
			"fieldname": "custom_neocharges_cb",
			"fieldtype": "Column Break",
			"insert_after": TEMPLATE_FIELD,
		},
		{
			"fieldname": JE_REFERENCE_FIELD,
			"label": "Charges Journal Entry",
			"fieldtype": "Data",
			"insert_after": "custom_neocharges_cb",
			"read_only": 1,
			"no_copy": 1,
			"allow_on_submit": 1,
			"depends_on": f"eval:doc.{JE_REFERENCE_FIELD}",
			"description": "Journal Entry created by this document's charge lines.",
		},
		{
			"fieldname": "custom_neocharges_sb",
			"fieldtype": "Section Break",
			"insert_after": JE_REFERENCE_FIELD,
			"depends_on": f"eval:doc.{APPLY_FIELD}",
		},
		{
			"fieldname": CHARGE_TABLE_FIELD,
			"label": "Charge Lines",
			"fieldtype": "Table",
			"options": CHARGE_ROW_DOCTYPE,
			"insert_after": "custom_neocharges_sb",
			"depends_on": f"eval:doc.{APPLY_FIELD}",
			"no_copy": 0,
		},
	]


JOURNAL_ENTRY_FIELDS = [
	{
		"fieldname": "custom_neocharge_source_section",
		"label": "Charge Source",
		"fieldtype": "Section Break",
		"insert_after": "user_remark",
		"collapsible": 1,
	},
	{
		"fieldname": "custom_neocharge_source_doctype",
		"label": "Charge Source Document Type",
		"fieldtype": "Link",
		"options": "DocType",
		"insert_after": "custom_neocharge_source_section",
		"read_only": 1,
		"no_copy": 1,
	},
	{
		"fieldname": "custom_neocharge_source_document",
		"label": "Charge Source Document",
		"fieldtype": "Dynamic Link",
		"options": "custom_neocharge_source_doctype",
		"insert_after": "custom_neocharge_source_doctype",
		"read_only": 1,
		"no_copy": 1,
		"search_index": 1,
	},
]


def enabled_doctypes():
	"""Document types currently switched on in Settings."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return []
	rows = frappe.get_all(
		"NeoCharge DocType Config",
		filters={"parent": SETTINGS_DOCTYPE, "enabled": 1},
		pluck="document_type",
	)
	return [d for d in rows if d in SUPPORTED_DOCTYPES]


def sync_custom_fields(doctypes=None):
	"""Create charge fields on the given (or all enabled) document types."""
	targets = doctypes if doctypes is not None else enabled_doctypes()

	spec = {"Journal Entry": JOURNAL_ENTRY_FIELDS}
	for doctype in targets:
		if doctype not in SUPPORTED_DOCTYPES:
			continue
		if not frappe.db.exists("DocType", doctype):
			continue
		spec[doctype] = transaction_fields(doctype)

	create_custom_fields(spec, ignore_validate=True)
	frappe.clear_cache()


def remove_custom_fields(doctypes):
	"""Drop charge fields from document types the user has switched off.

	Only removes fields with no data behind them; a document type that has
	already posted charges keeps its fields so history stays readable.
	"""
	fieldnames = [
		"custom_neocharges_section",
		APPLY_FIELD,
		TEMPLATE_FIELD,
		"custom_neocharges_cb",
		JE_REFERENCE_FIELD,
		"custom_neocharges_sb",
		CHARGE_TABLE_FIELD,
	]

	for doctype in doctypes:
		has_history = frappe.db.exists(
			CHARGE_ROW_DOCTYPE, {"parenttype": doctype}
		)
		if has_history:
			continue
		for fieldname in fieldnames:
			name = f"{doctype}-{fieldname}"
			if frappe.db.exists("Custom Field", name):
				frappe.delete_doc("Custom Field", name, ignore_permissions=True)

	frappe.clear_cache()

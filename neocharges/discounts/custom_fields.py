"""Idempotent custom fields for item discounts.

The discount section is placed at the end of the item row's native
"Discount and Margin" section, found at runtime rather than hard-coded: the
layout differs between sales and purchase item tables and between ERPNext
versions, and a Section Break inserted mid-section would drag native fields
into it.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from neocharges.constants import (
	DISCOUNT_CONFIG_DOCTYPE,
	DISCOUNT_DOCTYPES,
	ITEM_DOCTYPE_BY_PARENT,
	ND_ACTIVE,
	ND_DISC_1,
	ND_DISC_2,
	ND_EFFECTIVE,
	ND_EXTRA,
	ND_GROSS,
	ND_LINE,
	ND_LINE_AMOUNT,
	ND_MODE,
	ND_NET,
	ND_REASON,
	ND_TOTAL,
	ND_UNIT,
	ND_ZATCA_FLAG_SET,
	ND_ZATCA_REASON_SET,
	REASON_DOCTYPE,
	SETTINGS_DOCTYPE,
	ZATCA_DOCTYPES,
)

SECTION = "custom_nd_section"
COLUMN = "custom_nd_cb"


def _anchor(item_doctype):
	"""Last field before the Section Break that follows discount_amount."""
	meta = frappe.get_meta(item_doctype)
	fieldnames = [df.fieldname for df in meta.fields]
	if "discount_amount" not in fieldnames:
		return "rate"

	anchor = "discount_amount"
	for df in meta.fields[fieldnames.index("discount_amount") + 1 :]:
		if df.fieldtype in ("Section Break", "Tab Break"):
			break
		if df.fieldname.startswith("custom_nd_"):
			break
		anchor = df.fieldname
	return anchor


def item_fields(item_doctype):
	return [
		{
			"fieldname": SECTION,
			"label": "Item Discount",
			"fieldtype": "Section Break",
			"insert_after": _anchor(item_doctype),
			"collapsible": 1,
			"collapsible_depends_on": f"eval:doc.{ND_ACTIVE}",
		},
		{
			"fieldname": ND_GROSS,
			"label": "Gross Rate",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": SECTION,
			"description": "Rate before item discount, taken from the row.",
		},
		{
			"fieldname": ND_DISC_1,
			"label": "Discount 1 (%)",
			"fieldtype": "Percent",
			"insert_after": ND_GROSS,
		},
		{
			"fieldname": ND_DISC_2,
			"label": "Discount 2 (%)",
			"fieldtype": "Percent",
			"insert_after": ND_DISC_1,
			"description": "Applied to the balance after Discount 1.",
		},
		{
			"fieldname": ND_EXTRA,
			"label": "Extra Discount per Unit",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": ND_DISC_2,
		},
		{
			"fieldname": ND_LINE_AMOUNT,
			"label": "Discount Amount (Line)",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": ND_EXTRA,
			"description": "Fixed amount off the whole line, after the other discounts.",
		},
		{
			"fieldname": ND_REASON,
			"label": "Discount Reason",
			"fieldtype": "Link",
			"options": REASON_DOCTYPE,
			"insert_after": ND_LINE_AMOUNT,
		},
		{
			"fieldname": COLUMN,
			"fieldtype": "Column Break",
			"insert_after": ND_REASON,
		},
		{
			"fieldname": ND_EFFECTIVE,
			"label": "Effective Discount (%)",
			"fieldtype": "Percent",
			"insert_after": COLUMN,
			"read_only": 1,
			"precision": "3",
		},
		{
			"fieldname": ND_UNIT,
			"label": "Discount per Unit",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": ND_EFFECTIVE,
			"read_only": 1,
		},
		{
			"fieldname": ND_NET,
			"label": "Net Rate after Discount",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": ND_UNIT,
			"read_only": 1,
		},
		{
			"fieldname": ND_LINE,
			"label": "Line Discount",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": ND_NET,
			"read_only": 1,
		},
		{
			"fieldname": ND_ACTIVE,
			"label": "Item Discount Applied",
			"fieldtype": "Check",
			"insert_after": ND_LINE,
			"read_only": 1,
			"hidden": 1,
			"print_hide": 1,
		},
		{
			"fieldname": ND_MODE,
			"label": "Item Discount Method",
			"fieldtype": "Data",
			"insert_after": ND_ACTIVE,
			"read_only": 1,
			"hidden": 1,
			"print_hide": 1,
		},
	]


def parent_fields(doctype):
	fields = [
		{
			"fieldname": ND_TOTAL,
			"label": "Total Item Discount",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": "total",
			"read_only": 1,
			"description": "Sum of item discounts on this document.",
		}
	]
	if doctype in ZATCA_DOCTYPES:
		for fieldname, label in (
			(ND_ZATCA_FLAG_SET, "ZATCA Line Discount Flag Set by NeoCharges"),
			(ND_ZATCA_REASON_SET, "ZATCA Discount Reason Set by NeoCharges"),
		):
			fields.append(
				{
					"fieldname": fieldname,
					"label": label,
					"fieldtype": "Check",
					"insert_after": ND_TOTAL,
					"read_only": 1,
					"hidden": 1,
					"print_hide": 1,
				}
			)
	return fields


ITEM_FIELDNAMES = [
	SECTION,
	ND_GROSS,
	ND_DISC_1,
	ND_DISC_2,
	ND_EXTRA,
	ND_LINE_AMOUNT,
	ND_REASON,
	COLUMN,
	ND_EFFECTIVE,
	ND_UNIT,
	ND_NET,
	ND_LINE,
	ND_ACTIVE,
	ND_MODE,
]
PARENT_FIELDNAMES = [ND_TOTAL, ND_ZATCA_FLAG_SET, ND_ZATCA_REASON_SET]


def enabled_discount_doctypes():
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return []
	if not frappe.db.exists("DocType", DISCOUNT_CONFIG_DOCTYPE):
		return []
	if not frappe.db.get_single_value(SETTINGS_DOCTYPE, "enable_item_discounts"):
		return []
	rows = frappe.get_all(
		DISCOUNT_CONFIG_DOCTYPE,
		filters={"parent": SETTINGS_DOCTYPE, "enabled": 1},
		pluck="document_type",
	)
	return [d for d in rows if d in DISCOUNT_DOCTYPES]


def sync_discount_fields(doctypes=None):
	targets = doctypes if doctypes is not None else enabled_discount_doctypes()
	spec = {}
	for doctype in targets:
		item_doctype = ITEM_DOCTYPE_BY_PARENT.get(doctype)
		if not item_doctype:
			continue
		if not (frappe.db.exists("DocType", doctype) and frappe.db.exists("DocType", item_doctype)):
			continue
		spec[item_doctype] = item_fields(item_doctype)
		spec[doctype] = parent_fields(doctype)

	if spec:
		create_custom_fields(spec, ignore_validate=True)
		frappe.clear_cache()


def remove_discount_fields(doctypes):
	"""Drop discount fields only where no line ever carried a discount."""
	for doctype in doctypes:
		item_doctype = ITEM_DOCTYPE_BY_PARENT.get(doctype)
		if not item_doctype:
			continue
		if frappe.db.has_column(item_doctype, ND_ACTIVE) and frappe.db.exists(
			item_doctype, {ND_ACTIVE: 1}
		):
			continue

		for dt, names in ((item_doctype, ITEM_FIELDNAMES), (doctype, PARENT_FIELDNAMES)):
			for fieldname in names:
				name = f"{dt}-{fieldname}"
				if frappe.db.exists("Custom Field", name):
					frappe.delete_doc("Custom Field", name, ignore_permissions=True)

	frappe.clear_cache()

"""Charges billed to the customer as invoice lines.

Add To: Invoice Line puts the charge on the selling document as a service
item row. ERPNext then taxes it with the document's VAT, posts it to the
income account, and the ZATCA app reports it on the e-invoice like any other
line. This is the compliant way to bill a customer a charge that carries VAT:
output VAT booked through a separate Journal Entry is not on any e-invoice.

The row's rate, in document currency:

    document prices include VAT   -> the line's gross amount
    document prices exclude VAT   -> the line's net amount

so a VAT Exclusive or VAT Inclusive charge lands at the right net either way.
VAT itself is always computed by ERPNext from the document's taxes (or the
line's Item Tax Template), never posted separately, so it is on the invoice
and in the XML.

Rows are marked with the charge line's name. Only marked rows are inserted,
updated or removed, so the user's own items are never touched.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from neocharges.constants import (
	APPLY_TO_INVOICE_LINE,
	CHARGE_BASIS_PERCENTAGE,
	CHARGE_TABLE_FIELD,
	ITEM_DOCTYPE_BY_CHARGE_PARENT,
	ITEM_ROW_MARKER,
	SETTINGS_DOCTYPE,
	VAT_NONE,
)


def is_invoice_line(row):
	return (row.get("apply_to") or "") == APPLY_TO_INVOICE_LINE


def supported(doc):
	item_doctype = ITEM_DOCTYPE_BY_CHARGE_PARENT.get(doc.doctype)
	return bool(item_doctype) and frappe.get_meta(item_doctype).has_field(ITEM_ROW_MARKER)


def regular_items(doc):
	return [item for item in doc.get("items") or [] if not item.get(ITEM_ROW_MARKER)]


def regular_net_total(doc):
	"""Net total of the user's own items, the base for percentage charge lines."""
	total = 0.0
	for item in regular_items(doc):
		value = item.get("net_amount")
		if value in (None, 0) or flt(value) == 0:
			value = item.get("amount") or flt(item.get("qty")) * flt(item.get("rate"))
		total += flt(value)
	return total


def document_vat(doc):
	"""(rate, prices include VAT) from the document's first tax row."""
	taxes = doc.get("taxes") or []
	if not taxes:
		return 0.0, False
	first = taxes[0]
	return flt(first.get("rate")), bool(cint(first.get("included_in_print_rate")))


def _settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def _resolve(doc, row):
	"""Amount, currency and VAT split for one invoice-line charge."""
	from neocharges.charges.currency import resolve_line_currency
	from neocharges.charges.posting import resolve_amount
	from neocharges.charges.vat import resolve_line_vat

	if not row.get("charge_item"):
		row.charge_item = _settings().get("default_charge_item")
	row.amount = resolve_amount(doc, row)
	resolve_line_currency(doc, row)
	resolve_line_vat(doc, row, _settings(), _precision(doc))


def _precision(doc):
	return doc.precision("net_total") if doc.meta.has_field("net_total") else 2


def validate_line(doc, row):
	label = _("Charge row {0}").format(row.idx)

	if not supported(doc):
		frappe.throw(_("{0}: Add To 'Invoice Line' is available on selling documents only.").format(label))

	if row.charge_basis == CHARGE_BASIS_PERCENTAGE and (row.percentage_on or "Net Total") != "Net Total":
		frappe.throw(
			_(
				"{0}: an invoice-line charge adds to the invoice's own totals, so it can only be a "
				"percentage of Net Total."
			).format(label)
		)

	if not row.get("charge_item"):
		frappe.throw(
			_(
				"{0}: choose a Charge Item (a non-stock service item), or set a Default Charge Item in "
				"NeoCharges Settings."
			).format(label)
		)

	item = frappe.get_cached_value(
		"Item", row.charge_item, ["is_stock_item", "is_fixed_asset", "disabled", "stock_uom"], as_dict=True
	)
	if not item:
		frappe.throw(_("{0}: item {1} does not exist").format(label, row.charge_item))
	if item.is_stock_item or item.is_fixed_asset or item.disabled:
		frappe.throw(
			_("{0}: {1} must be an enabled, non-stock service item.").format(label, row.charge_item)
		)

	if row.get("item_tax_template"):
		return

	vat_rate, _inclusive = document_vat(doc)
	treatment = row.get("vat_treatment") or VAT_NONE
	if treatment == VAT_NONE and vat_rate:
		frappe.throw(
			_(
				"{0}: this charge has no VAT but the invoice charges {1}% VAT on every line. For a "
				"zero-rated or exempt charge, set an Item Tax Template on the charge line."
			).format(label, vat_rate)
		)
	if treatment != VAT_NONE and abs(flt(row.get("vat_rate")) - vat_rate) > 0.0001:
		frappe.throw(
			_(
				"{0}: the charge's VAT rate {1}% differs from the invoice's {2}%. On the invoice the "
				"line is taxed by the invoice's VAT, so set the same rate, or an Item Tax Template."
			).format(label, flt(row.get("vat_rate")), vat_rate)
		)


def _row_values(doc, row):
	from neocharges.charges.currency import document_currency, document_rate, to_document_currency

	_rate, inclusive = document_vat(doc)
	precision = doc.precision("rate", "items") if hasattr(doc, "precision") else 2
	if inclusive:
		value = to_document_currency(
			row, document_currency(doc), document_rate(doc), precision,
			line_value=row.gross_amount, base_value=row.base_amount,
		)
	else:
		value = to_document_currency(
			row, document_currency(doc), document_rate(doc), precision,
			line_value=row.net_amount, base_value=row.base_net_amount,
		)

	stock_uom = frappe.get_cached_value("Item", row.charge_item, "stock_uom")
	qty = -1 if cint(doc.get("is_return")) else 1
	values = {
		"item_code": row.charge_item,
		"item_name": row.charge_description,
		"description": row.charge_description,
		"qty": qty,
		"uom": stock_uom,
		"stock_uom": stock_uom,
		"conversion_factor": 1,
		"price_list_rate": value,
		"rate": value,
		"amount": flt(value * qty, precision),
		"item_tax_template": row.get("item_tax_template") or None,
	}
	if row.get("account_head"):
		values["income_account"] = row.account_head
	cost_center = row.get("cost_center") or doc.get("cost_center")
	if cost_center:
		values["cost_center"] = cost_center
	if row.get("project"):
		values["project"] = row.project
	return values


def sync_invoice_lines(doc, method=None):
	"""Insert, update and remove the item rows this app owns. Returns True if changed."""
	if not supported(doc):
		return False

	rows = [row for row in doc.get(CHARGE_TABLE_FIELD) or [] if is_invoice_line(row)]
	if not doc.get("custom_apply_neocharges"):
		rows = []

	wanted = {}
	for row in rows:
		_resolve(doc, row)
		if row.get("charge_item") and flt(row.amount):
			wanted[row.name] = _row_values(doc, row)

	changed = False
	kept = []
	for item in doc.get("items") or []:
		marker = item.get(ITEM_ROW_MARKER)
		if marker and marker not in wanted:
			changed = True
			continue
		kept.append(item)
	if len(kept) != len(doc.get("items") or []):
		doc.set("items", kept)

	existing = {item.get(ITEM_ROW_MARKER): item for item in doc.get("items") or [] if item.get(ITEM_ROW_MARKER)}
	for name, values in wanted.items():
		item = existing.get(name)
		if item is None:
			item = doc.append("items", {})
			item.set(ITEM_ROW_MARKER, name)
			changed = True
		for field, value in values.items():
			if item.meta.has_field(field) and item.get(field) != value:
				item.set(field, value)
				changed = True

	if changed:
		for idx, item in enumerate(doc.get("items") or [], start=1):
			item.idx = idx
	return changed


def before_validate(doc, method=None):
	"""Place the rows before the controller totals the document."""
	if doc.docstatus == 0:
		sync_invoice_lines(doc)

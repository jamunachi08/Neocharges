"""Sync charge lines into the source document's native Taxes and Charges table.

A charge row with `apply_to` other than `None` stops being a standalone memo
pair and becomes a real ERPNext tax row. That is deliberate: ERPNext already
knows how to add a charge to the total, to stock valuation, or to both, and how
to distribute it across item rows. Reimplementing any of that would fight the
framework at every turn — on returns, reposts, perpetual inventory and stock
reconciliation.

Distribution maps onto native charge types:

    Amount    -> "Actual"            spread proportionally to item.net_amount
    Quantity  -> "On Item Quantity"  per-unit rate multiplied by item.qty

Sales documents have no `category` field on their tax rows, so valuation is
purchase-only. That is an ERPNext constraint, not ours.
"""

import frappe
from frappe import _
from frappe.utils import flt

from neocharges.constants import (
	APPLY_TO_NONE,
	CHARGE_TABLE_FIELD,
	CHARGE_TYPE_BY_DISTRIBUTION,
	DISTRIBUTE_BY_QUANTITY,
	PURCHASE_DOCTYPES,
	TAX_ROW_MARKER,
	VALUATION_APPLY_TO,
	ZATCA_DOCTYPES,
)


def is_native_row(row):
	return bool(row.apply_to) and row.apply_to != APPLY_TO_NONE


def split_rows(doc):
	"""Return (memo_rows, native_rows) from the charge table."""
	memo, native = [], []
	for row in doc.get(CHARGE_TABLE_FIELD) or []:
		(native if is_native_row(row) else memo).append(row)
	return memo, native


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_native_row(doc, row):
	label = _("Charge row {0}").format(row.idx)

	if not doc.meta.has_field("taxes"):
		frappe.throw(
			_("{0}: {1} has no Taxes and Charges table, so 'Add To' must be None.").format(
				label, _(doc.doctype)
			)
		)

	if not row.account_head:
		frappe.throw(_("{0}: Account Head is required when Add To is not None").format(label))

	_guard_zatca_tax_table(doc, label)

	if row.apply_to in VALUATION_APPLY_TO and doc.doctype not in PURCHASE_DOCTYPES:
		frappe.throw(
			_("{0}: valuation charges only exist on purchase documents. "
			  "Sales tax rows have no category field in ERPNext.").format(label)
		)

	if row.percentage_on == "Grand Total":
		frappe.throw(
			_("{0}: a charge that adds to the total cannot be a percentage of Grand Total — "
			  "the charge would change the very figure it is computed from. "
			  "Use Net Total instead.").format(label)
		)

	if row.distribute_based_on == DISTRIBUTE_BY_QUANTITY:
		total_qty = sum(flt(item.get("qty")) for item in doc.get("items") or [])
		if total_qty <= 0:
			frappe.throw(
				_("{0}: quantity-based distribution needs a total item quantity above zero.").format(
					label
				)
			)

	account = frappe.get_cached_value(
		"Account", row.account_head, ["company", "is_group", "disabled"], as_dict=True
	)
	if not account:
		frappe.throw(_("{0}: account {1} does not exist").format(label, row.account_head))
	if account.is_group:
		frappe.throw(_("{0}: {1} is a group account").format(label, row.account_head))
	if account.disabled:
		frappe.throw(_("{0}: account {1} is disabled").format(label, row.account_head))
	if account.company != doc.company:
		frappe.throw(
			_("{0}: account {1} belongs to {2}, not {3}").format(
				label, row.account_head, account.company, doc.company
			)
		)

	# Memo-mode fields are meaningless here — clear them so nothing stale posts.
	row.debit_account = None
	row.credit_account = None
	row.party_type = None
	row.party = None
	row.party_side = None


def _guard_zatca_tax_table(doc, label):
	"""ZATCA apps read the Sales Taxes and Charges table as VAT.

	A charge row there is reported as a tax line (and the apps take the first
	row's rate as the invoice VAT rate), so the XML is wrong or rejected.
	Memo-mode charges never touch the invoice and are unaffected.
	"""
	if doc.doctype not in ZATCA_DOCTYPES:
		return

	from neocharges.charges.posting import get_settings
	from neocharges.discounts.zatca import zatca_app_installed

	if not frappe.utils.cint(get_settings().get("block_zatca_tax_charges")):
		return
	if not zatca_app_installed():
		return

	frappe.throw(
		_(
			"{0}: on a ZATCA invoice, a charge with Add To other than None would be reported to ZATCA "
			"as VAT. Set Add To to None (the charge posts its own entry and leaves the invoice "
			"untouched), or add it as a normal invoice item instead."
		).format(label),
		title=_("ZATCA"),
	)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
def sync_tax_rows(doc, native_rows):
	"""Insert, update and prune the tax rows this app owns.

	Rows carry a marker holding the charge row's name, so the app only ever
	touches its own rows and leaves the user's tax table alone.
	"""
	if not doc.meta.has_field("taxes"):
		return False

	live = {row.name: row for row in native_rows}
	changed = False

	# Prune rows whose charge line is gone or switched back to memo mode.
	kept = []
	for tax in doc.get("taxes") or []:
		marker = tax.get(TAX_ROW_MARKER)
		if marker and marker not in live:
			changed = True
			continue
		kept.append(tax)

	if len(kept) != len(doc.get("taxes") or []):
		doc.set("taxes", kept)

	existing = {
		tax.get(TAX_ROW_MARKER): tax for tax in doc.get("taxes") or [] if tax.get(TAX_ROW_MARKER)
	}

	for row in native_rows:
		values = _tax_values(doc, row)
		tax = existing.get(row.name)

		if tax is None:
			tax = doc.append("taxes", {})
			tax.set(TAX_ROW_MARKER, row.name)
			changed = True

		for field, value in values.items():
			if tax.get(field) != value:
				tax.set(field, value)
				changed = True

	if changed:
		for idx, tax in enumerate(doc.get("taxes") or [], start=1):
			tax.idx = idx

	return changed


def _tax_values(doc, row):
	"""Tax-row values. ERPNext reads tax_amount in document currency.

	A line in another currency (SAR customs on a USD invoice) is converted
	with the document's rate, the only rate ERPNext applies to a taxes row.
	"""
	from neocharges.charges.currency import (
		document_currency,
		document_rate,
		resolve_line_currency,
		to_document_currency,
	)

	if not row.get("charge_currency"):
		resolve_line_currency(doc, row)

	distribution = row.distribute_based_on or "Amount"
	charge_type = CHARGE_TYPE_BY_DISTRIBUTION.get(distribution, "Actual")
	precision = doc.precision("total_taxes_and_charges") if doc.meta.has_field("total_taxes_and_charges") else 2
	amount = to_document_currency(row, document_currency(doc), document_rate(doc), precision)

	values = {
		"charge_type": charge_type,
		"account_head": row.account_head,
		"description": row.charge_description,
		"cost_center": row.cost_center or doc.get("cost_center"),
	}

	if charge_type == "On Item Quantity":
		# ERPNext multiplies this rate by each item's qty, so the per-unit rate
		# is what reproduces the requested total.
		total_qty = sum(flt(item.get("qty")) for item in doc.get("items") or [])
		values["rate"] = flt(amount / total_qty, 9) if total_qty else 0
		values["tax_amount"] = 0
	else:
		values["rate"] = 0
		values["tax_amount"] = amount

	if doc.doctype in PURCHASE_DOCTYPES:
		values["category"] = row.apply_to
		values["add_deduct_tax"] = "Add"

	return values

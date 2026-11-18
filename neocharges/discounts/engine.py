"""Item-wise discounts: apply, verify, authorise.

Two methods, chosen in NeoCharges Settings
-------------------------------------------
Keep Rate (discount on net amount), the default on ERPNext v16
    Rate and Amount stay exactly as entered. The discount for each line is
    written into ERPNext's own per-item discount field
    `mapped_additional_discount_amount` (per stock unit, 9 decimals). ERPNext
    then reduces that line's net amount, computes VAT on it, posts the line's
    income at the discounted figure, and carries the total as the document's
    Additional Discount on Net Total. Nothing is posted separately.

Reduce Rate
    The discount is taken off the rate:
        price_list_rate = gross, discount_amount = discount per unit,
        rate = net, discount_percentage = 0.
    Available on v15 and v16.

`discount_percentage` stays zero in Reduce Rate: ERPNext v15 and v16 both
recompute `rate` from a non-zero percentage under some conditions, which would
undo the rounding chosen here.

Timing
------
`before_validate` runs ahead of the controller's own validate, so ERPNext
totals the document, builds the payment schedule and runs its checks from the
discounted figures in the normal way. `validate` then verifies that nothing in
between moved a line, and stops the save if something did.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from neocharges.constants import (
	AUTHORITY_ALL,
	DISCOUNT_DOCTYPES,
	DISCOUNT_SALES_DOCTYPES,
	DISTRIBUTED_DISCOUNT_FIELD,
	ITEM_DOCTYPE_BY_PARENT,
	MAPPED_DISCOUNT_FIELD,
	METHOD_KEEP,
	MODE_KEEP,
	MODE_REDUCE,
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
	REASON_DOCTYPE,
	ROUNDING_DOCUMENT,
	SETTINGS_DOCTYPE,
)
from neocharges.discounts import calc

COMPUTED_FIELDS = (ND_EFFECTIVE, ND_UNIT, ND_LINE, ND_NET)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def get_settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def is_discount_enabled(doctype):
	if doctype not in DISCOUNT_DOCTYPES:
		return False
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return False

	settings = get_settings()
	if not cint(settings.get("enable_item_discounts")):
		return False

	enabled = any(
		row.document_type == doctype and cint(row.enabled)
		for row in settings.get("discount_doctypes") or []
	)
	if not enabled:
		return False

	# Fields not synced yet (e.g. mid-migrate): stay out of the way.
	item_doctype = ITEM_DOCTYPE_BY_PARENT.get(doctype)
	return bool(item_doctype) and frappe.get_meta(item_doctype).has_field(ND_ACTIVE)


def keep_rate_supported(doctype="Sales Invoice"):
	"""Keep Rate needs ERPNext's per-item discount (v16)."""
	item_doctype = ITEM_DOCTYPE_BY_PARENT.get(doctype, "Sales Invoice Item")
	if not frappe.get_meta(item_doctype).has_field(MAPPED_DISCOUNT_FIELD):
		return False
	try:
		from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals
	except ImportError:
		return False
	return hasattr(calculate_taxes_and_totals, "apply_mapped_additional_discount")


def discount_mode(settings):
	return MODE_KEEP if (settings.get("discount_method") or "") == METHOD_KEEP else MODE_REDUCE


def rate_precision(item, settings):
	"""Precision for gross, discount and net rate.

	Never finer than the rate field itself: ERPNext rounds every item float to
	its field precision, and a finer figure would not survive that.
	"""
	field_precision = cint(item.precision("rate"))
	if (settings.get("discount_rounding") or "") == ROUNDING_DOCUMENT:
		return field_precision or 2
	return min(2, field_precision) if field_precision else 2


def _inputs(item):
	return (
		flt(item.get(ND_DISC_1)),
		flt(item.get(ND_DISC_2)),
		flt(item.get(ND_EXTRA)),
		flt(item.get(ND_LINE_AMOUNT)),
	)


def has_discount_input(item):
	return calc.has_input(*_inputs(item))


def stock_qty(item):
	return flt(item.get("qty")) * (flt(item.get("conversion_factor")) or 1)


# ---------------------------------------------------------------------------
# before_validate: apply
# ---------------------------------------------------------------------------
def apply_item_discounts(doc, method=None):
	if not is_discount_enabled(doc.doctype):
		return
	if doc.get("is_consolidated"):
		# Consolidated POS invoices carry rows already resolved on the POS
		# Invoices they merge; ERPNext skips recalculating them and so do we.
		return

	settings = get_settings()
	mode = discount_mode(settings)
	items = doc.get("items") or []
	any_input = any(has_discount_input(item) for item in items)

	if mode == MODE_KEEP and any_input:
		if not keep_rate_supported(doc.doctype):
			frappe.throw(
				_(
					"The 'Keep Rate' discount method needs ERPNext's per-item discount, available from "
					"ERPNext v16. Update ERPNext, or switch NeoCharges Settings → Discount Method to "
					"'Reduce Rate'."
				),
				title=_("Item Discount"),
			)
		_guard_header_discount(doc)

	total = 0.0
	released_keep = False
	for item in items:
		if has_discount_input(item):
			if mode == MODE_KEEP:
				_apply_keep_line(doc, item, settings)
			else:
				released_keep |= _apply_reduce_line(doc, item, settings)
			total += flt(item.get(ND_LINE))
		else:
			if cint(item.get(ND_ACTIVE)):
				released_keep |= _release_line(item)
			_fill_gross(item, mode)

	if mode == MODE_KEEP or released_keep:
		_sync_document_discount(doc, released_keep)

	if doc.meta.has_field(ND_TOTAL):
		doc.set(ND_TOTAL, flt(total, doc.precision(ND_TOTAL) or 2))

	from neocharges.discounts import zatca

	zatca.sync_document(doc, settings)


def _label(item):
	return _("Row {0} ({1})").format(item.idx, item.item_code or _("no item"))


def _fill_gross(item, mode):
	"""Show the row's own figure in Gross Rate before any discount is typed."""
	if mode == MODE_KEEP:
		gross = flt(item.get("rate"))
	else:
		gross = flt(item.get("price_list_rate")) or flt(item.get("rate"))
	if gross and flt(item.get(ND_GROSS)) != gross:
		item.set(ND_GROSS, gross)
	if gross and flt(item.get(ND_NET)) != gross:
		item.set(ND_NET, gross)


# ----- Keep Rate -----------------------------------------------------------
def _guard_header_discount(doc):
	"""Keep Rate carries item discounts in the document's Additional Discount.

	ERPNext clears per-item discounts whenever the header discount is edited
	by hand, so the two cannot be combined. Refuse rather than silently
	losing one of them.
	"""
	has_mapped = any(flt(item.get(MAPPED_DISCOUNT_FIELD)) for item in doc.get("items") or [])
	manual = (
		flt(doc.get("additional_discount_percentage"))
		or (cint(doc.get("is_cash_or_non_trade_discount")) and flt(doc.get("discount_amount")))
		or (flt(doc.get("discount_amount")) and not has_mapped)
	)
	if manual:
		frappe.throw(
			_(
				"This document has an Additional Discount and item discounts. With the 'Keep Rate' "
				"method, item discounts are carried in the Additional Discount fields, so both cannot "
				"be used together. Clear the Additional Discount and enter it as item discounts, or "
				"clear the item discounts."
			),
			title=_("Item Discount"),
		)


def _apply_keep_line(doc, item, settings):
	label = _label(item)

	if cint(item.get(ND_ACTIVE)) and (item.get(ND_MODE) or MODE_REDUCE) == MODE_REDUCE:
		# Applied earlier under Reduce Rate: put the rate back first.
		_restore_reduced_rate(item)

	if cint(item.get("is_free_item")):
		frappe.throw(_("{0}: free items cannot take an item discount.").format(label))

	gross = flt(item.get("rate"))
	if gross <= 0:
		frappe.throw(_("{0}: enter a rate before applying a discount.").format(label))

	pct1, pct2, extra, line_amount = _inputs(item)
	try:
		result = calc.compute_line_keep(
			gross,
			flt(item.get("qty")),
			pct1,
			pct2,
			extra,
			line_amount,
			rnd=flt,
			rate_precision=cint(item.precision("rate")) or 2,
			amount_precision=item.precision("amount") or 2,
			allow_full=cint(settings.get("allow_full_discount")),
		)
	except calc.DiscountError as exc:
		frappe.throw(_discount_error_message(label, exc))

	reason = _resolve_reason(doc, item, settings, label)
	_check_reason_cap(reason, result, label)
	_check_item_max_discount(doc, item, result, label)

	qty_stock = stock_qty(item)
	mapped = flt(abs(result.line_discount) / abs(qty_stock), 9) if qty_stock else 0.0
	item.set(MAPPED_DISCOUNT_FIELD, mapped)
	item.set(
		DISTRIBUTED_DISCOUNT_FIELD,
		flt(mapped * qty_stock, item.precision(DISTRIBUTED_DISCOUNT_FIELD) or 2),
	)

	item.set(ND_GROSS, result.gross)
	item.set(ND_NET, result.net)
	item.set(ND_UNIT, result.unit_discount)
	item.set(ND_EFFECTIVE, result.effective_pct)
	item.set(ND_LINE, result.line_discount)
	item.set(ND_MODE, MODE_KEEP)
	item.set(ND_ACTIVE, 1)


def _sync_document_discount(doc, released):
	"""Make the header discount exactly the sum of the per-item discounts.

	ERPNext's validate checks that the header is 'derived' from the items
	(Net Total, no percentage, amount equal to the sum of distributed
	discounts) and clears the per-item discounts otherwise. Setting the
	distributed figures here keeps that check true from the first pass.
	"""
	items = doc.get("items") or []
	has_mapped = False
	for item in items:
		mapped = flt(item.get(MAPPED_DISCOUNT_FIELD))
		if mapped:
			has_mapped = True
			item.set(
				DISTRIBUTED_DISCOUNT_FIELD,
				flt(mapped * stock_qty(item), item.precision(DISTRIBUTED_DISCOUNT_FIELD) or 2),
			)
		elif item.meta.has_field(DISTRIBUTED_DISCOUNT_FIELD):
			item.set(DISTRIBUTED_DISCOUNT_FIELD, 0)

	if has_mapped:
		doc.apply_discount_on = "Net Total"
		doc.additional_discount_percentage = 0
		doc.discount_amount = flt(
			sum(flt(item.get(DISTRIBUTED_DISCOUNT_FIELD)) for item in items),
			doc.precision("discount_amount"),
		)
		_set_additional_discount_account(doc)
	elif released and doc.meta.has_field("discount_amount"):
		doc.discount_amount = 0


def _set_additional_discount_account(doc):
	"""Fill the document's discount account from the reasons, if unambiguous."""
	if not doc.meta.has_field("additional_discount_account") or doc.get("additional_discount_account"):
		return
	accounts = set()
	for item in doc.get("items") or []:
		if not cint(item.get(ND_ACTIVE)) or not item.get(ND_REASON):
			continue
		reason = frappe.get_cached_doc(REASON_DOCTYPE, item.get(ND_REASON))
		for row in reason.get("accounts") or []:
			if row.company == doc.company and row.discount_account:
				accounts.add(row.discount_account)
	if len(accounts) == 1:
		doc.additional_discount_account = accounts.pop()


# ----- Reduce Rate ---------------------------------------------------------
def _apply_reduce_line(doc, item, settings):
	"""Returns True if the line was switched over from Keep Rate."""
	label = _label(item)
	switched = False

	if cint(item.get(ND_ACTIVE)) and item.get(ND_MODE) == MODE_KEEP:
		_clear_mapped(item)
		switched = True

	if cint(item.get("is_free_item")):
		frappe.throw(
			_("{0}: this is a free item added by a pricing rule. Item discounts cannot be applied to it.").format(
				label
			)
		)

	applied_rules = _applied_pricing_rules(item)
	if applied_rules and not cint(doc.get("ignore_pricing_rule")):
		frappe.throw(
			_(
				"{0}: pricing rule {1} already prices this line. Tick 'Ignore Pricing Rule' on the "
				"document, or clear the item discount fields on this row. The two cannot be combined "
				"because ERPNext re-applies the rule after this discount is set."
			).format(label, ", ".join(applied_rules))
		)

	if item.get("margin_type") and flt(item.get("margin_rate_or_amount")):
		frappe.throw(
			_("{0}: remove the margin first. A line can carry a margin or an item discount, not both.").format(
				label
			)
		)

	gross = flt(item.get(ND_GROSS)) or flt(item.get("price_list_rate"))
	if not gross and not cint(item.get(ND_ACTIVE)):
		# First application on a line with no price list: the typed rate is the gross.
		gross = flt(item.get("rate"))
	if gross <= 0:
		frappe.throw(_("{0}: set a Gross Rate before applying a discount.").format(label))

	precision = rate_precision(item, settings)
	pct1, pct2, extra, line_amount = _inputs(item)

	try:
		result = calc.compute_line(
			gross,
			flt(item.get("qty")),
			pct1,
			pct2,
			extra,
			line_amount,
			rnd=flt,
			rate_precision=precision,
			amount_precision=item.precision("amount") or 2,
			allow_full=cint(settings.get("allow_full_discount")),
		)
	except calc.DiscountError as exc:
		frappe.throw(_discount_error_message(label, exc))

	reason = _resolve_reason(doc, item, settings, label)
	_check_reason_cap(reason, result, label)
	_check_item_max_discount(doc, item, result, label)

	item.set(ND_GROSS, result.gross)
	item.price_list_rate = result.gross
	item.discount_percentage = 0
	item.discount_amount = result.unit_discount
	item.rate = result.net
	item.set(ND_NET, result.net)
	item.set(ND_UNIT, result.unit_discount)
	item.set(ND_EFFECTIVE, result.effective_pct)
	item.set(ND_LINE, result.line_discount)
	item.set(ND_MODE, MODE_REDUCE)
	item.set(ND_ACTIVE, 1)

	if line_amount and pct1 == pct2 == extra == 0:
		asked = line_amount if flt(item.get("qty")) >= 0 else -line_amount
		if abs(result.line_discount - asked) >= 0.005:
			frappe.msgprint(
				_(
					"{0}: with the 'Reduce Rate' method the discount is per unit, so {1} over {2} units "
					"comes to {3}. The 'Keep Rate' method applies a line amount exactly."
				).format(label, line_amount, abs(flt(item.get("qty"))), abs(result.line_discount)),
				indicator="orange",
				alert=True,
			)

	_set_discount_account(doc, item, reason)
	return switched


# ----- Release -------------------------------------------------------------
def _release_line(item):
	"""Discount inputs were cleared. Returns True if a Keep Rate line was released."""
	mode = item.get(ND_MODE) or MODE_REDUCE
	if mode == MODE_KEEP:
		_clear_mapped(item)
	else:
		_restore_reduced_rate(item)
	for field in COMPUTED_FIELDS:
		item.set(field, 0)
	item.set(ND_ACTIVE, 0)
	item.set(ND_MODE, None)
	return mode == MODE_KEEP


def _restore_reduced_rate(item):
	gross = flt(item.get(ND_GROSS))
	if gross > 0:
		item.price_list_rate = gross
		item.rate = gross
	item.discount_percentage = 0
	item.discount_amount = 0


def _clear_mapped(item):
	if item.meta.has_field(MAPPED_DISCOUNT_FIELD):
		item.set(MAPPED_DISCOUNT_FIELD, 0)
	if item.meta.has_field(DISTRIBUTED_DISCOUNT_FIELD):
		item.set(DISTRIBUTED_DISCOUNT_FIELD, 0)


# ---------------------------------------------------------------------------
# Shared checks
# ---------------------------------------------------------------------------
def _applied_pricing_rules(item):
	raw = item.get("pricing_rules")
	if not raw:
		return []
	try:
		rules = json.loads(raw)
	except (TypeError, ValueError):
		rules = [part.strip() for part in str(raw).split(",")]
	return [rule for rule in rules or [] if rule]


def _discount_error_message(label, exc):
	if exc.code == calc.ERR_GROSS:
		return _("{0}: set a rate before applying a discount.").format(label)
	if exc.code == calc.ERR_PERCENT:
		return _("{0}: discount percentages must be between 0 and 100.").format(label)
	if exc.code == calc.ERR_EXTRA:
		return _("{0}: the extra discount per unit cannot be negative.").format(label)
	if exc.code == calc.ERR_LINE:
		return _("{0}: the line discount amount cannot be negative.").format(label)
	if exc.code == calc.ERR_QTY:
		return _("{0}: a line discount amount needs a quantity.").format(label)
	if exc.code == calc.ERR_NEGATIVE:
		return _("{0}: the discounts add up to more than the line value.").format(label)
	if exc.code == calc.ERR_FULL:
		return _(
			"{0}: this discount brings the line to zero. Enable 'Allow 100% Line Discount' in "
			"NeoCharges Settings if that is intended."
		).format(label)
	return _("{0}: invalid discount").format(label)


def _resolve_reason(doc, item, settings, label):
	name = item.get(ND_REASON)
	if not name and settings.get("default_discount_reason"):
		name = settings.default_discount_reason
		item.set(ND_REASON, name)

	if not name:
		if cint(settings.get("require_discount_reason")):
			frappe.throw(_("{0}: select a Discount Reason.").format(label))
		return None

	reason = frappe.get_cached_doc(REASON_DOCTYPE, name)
	if cint(reason.disabled):
		frappe.throw(_("{0}: discount reason {1} is disabled.").format(label, name))
	return reason


def _check_reason_cap(reason, result, label):
	if not reason:
		return
	cap = flt(reason.get("max_discount_pct"))
	if cap and result.effective_pct > cap:
		frappe.throw(
			_("{0}: effective discount {1}% exceeds the {2}% allowed for reason {3}.").format(
				label, result.effective_pct, cap, reason.name
			)
		)


def _check_item_max_discount(doc, item, result, label):
	"""Mirror ERPNext's Item.max_discount check.

	ERPNext validates `discount_percentage`, which this app keeps at zero in
	both methods, so without this check the item master's cap would silently
	stop applying.
	"""
	if doc.doctype not in DISCOUNT_SALES_DOCTYPES:
		return
	if doc.get("is_return") or doc.get("is_debit_note"):
		return
	if not item.item_code:
		return
	cap = flt(frappe.get_cached_value("Item", item.item_code, "max_discount"))
	if cap and result.effective_pct > cap:
		frappe.throw(
			_("{0}: maximum discount for item {1} is {2}%. This line is at {3}%.").format(
				label, item.item_code, cap, result.effective_pct
			)
		)


def _set_discount_account(doc, item, reason):
	"""Reduce Rate: route the per-unit discount to the reason's account.

	Only fills an empty field. ERPNext uses it when Selling Settings ->
	Enable Discount Accounting is on.
	"""
	if not reason or not item.meta.has_field("discount_account"):
		return
	if item.get("discount_account"):
		return
	for row in reason.get("accounts") or []:
		if row.company == doc.company and row.discount_account:
			item.discount_account = row.discount_account
			return


# ---------------------------------------------------------------------------
# validate: verify
# ---------------------------------------------------------------------------
def verify_item_discounts(doc, method=None):
	"""Fail loudly if anything between before_validate and now moved a line.

	A pricing rule fetched during validate, a header discount edit, or
	another app's hook could rewrite a rate or clear a per-item discount.
	Posting that would put wrong figures on the invoice and the ZATCA XML.
	"""
	if not is_discount_enabled(doc.doctype) or doc.get("is_consolidated"):
		return

	settings = get_settings()
	for item in doc.get("items") or []:
		if not cint(item.get(ND_ACTIVE)):
			continue

		if item.get(ND_MODE) == MODE_KEEP:
			tolerance = 0.5 / (10 ** (cint(item.precision("rate")) or 2))
			if abs(flt(item.rate) - flt(item.get(ND_GROSS))) > tolerance:
				_conflict(item, flt(item.rate), flt(item.get(ND_GROSS)))
			if flt(item.get(ND_LINE)) and not flt(item.get(MAPPED_DISCOUNT_FIELD)):
				frappe.throw(
					_(
						"{0}: the item discount was cleared while saving, most likely because the "
						"document's Additional Discount was changed. Clear the Additional Discount and "
						"save again."
					).format(_label(item)),
					title=_("Item Discount Conflict"),
				)
			continue

		precision = rate_precision(item, settings)
		tolerance = 0.5 / (10**precision)
		expected = flt(flt(item.get(ND_GROSS)) - flt(item.get(ND_UNIT)), precision)
		if abs(flt(item.rate) - expected) > tolerance or abs(
			flt(item.discount_amount) - flt(item.get(ND_UNIT))
		) > tolerance:
			_conflict(item, flt(item.rate), expected)


def _conflict(item, found, expected):
	frappe.throw(
		_(
			"{0}: the rate was changed to {1} after the item discount was set on {2}. "
			"Another rule or app is repricing this line. Remove the conflicting rule or the "
			"item discount, then save again."
		).format(_label(item), found, expected),
		title=_("Item Discount Conflict"),
	)


# ---------------------------------------------------------------------------
# before_submit: authority
# ---------------------------------------------------------------------------
def check_discount_authority(doc, method=None):
	"""Block submission of discounts above the submitting user's limit.

	Drafts save freely so a salesperson can prepare a quotation; the person
	who submits must hold a role whose limit covers every line.
	"""
	if not is_discount_enabled(doc.doctype):
		return
	if doc.get("is_return") or doc.get("is_consolidated"):
		return

	settings = get_settings()
	authorities = settings.get("discount_authorities") or []
	if not authorities:
		return

	scope = settings.get("authority_scope") or ""
	if scope != AUTHORITY_ALL and doc.doctype not in DISCOUNT_SALES_DOCTYPES:
		return

	if frappe.session.user == "Administrator":
		return

	roles = set(frappe.get_roles())
	limits = [flt(row.max_discount_pct) for row in authorities if row.role in roles]
	limit = max(limits) if limits else 0.0

	over = [
		item
		for item in doc.get("items") or []
		if cint(item.get(ND_ACTIVE)) and flt(item.get(ND_EFFECTIVE)) > limit
	]
	if not over:
		return

	lines = "".join(
		"<li>{0}: {1}%</li>".format(_label(item), flt(item.get(ND_EFFECTIVE))) for item in over
	)
	frappe.throw(
		_("Your discount authority is {0}%. These lines exceed it:").format(limit)
		+ f"<ul>{lines}</ul>"
		+ _("Ask a user with a higher limit to submit this document."),
		title=_("Discount Above Your Authority"),
	)

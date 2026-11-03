"""Item-wise discounts: apply, verify, authorise.

Design
------
The discount inputs (two cascading percentages and a fixed amount per unit)
live in this app's own item fields, but every *result* is written into
ERPNext's native item fields:

    price_list_rate      = gross rate (before discount)
    discount_percentage  = 0
    discount_amount      = discount per unit
    rate                 = net rate (after discount)

That is the whole integration strategy. ERPNext's totals, VAT, returns,
discount accounting (Selling Settings -> Enable Discount Accounting) and
reports all read those native fields, and so do ZATCA XML builders. Nothing
downstream has to know this app exists.

`discount_percentage` is deliberately left at zero. ERPNext v15 and v16 both
recompute `rate` from a non-zero percentage under some conditions (pricing
rules in v15, any mismatch in v16), which would undo the rounding this app
chose. With the percentage at zero and rate == price_list_rate -
discount_amount, both versions keep the rate as given. The effective
percentage is shown in this app's own read-only field instead.

Timing
------
`before_validate` runs before the controller's own validate, so ERPNext
calculates totals, VAT, payment schedule, selling-price checks and the
in-words fields from the discounted rates in the normal way. `validate`
then only verifies that nothing in between changed the result.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from neocharges.constants import (
	AUTHORITY_ALL,
	DISCOUNT_DOCTYPES,
	DISCOUNT_SALES_DOCTYPES,
	ITEM_DOCTYPE_BY_PARENT,
	ND_ACTIVE,
	ND_DISC_1,
	ND_DISC_2,
	ND_EFFECTIVE,
	ND_EXTRA,
	ND_GROSS,
	ND_LINE,
	ND_REASON,
	ND_TOTAL,
	ND_UNIT,
	REASON_DOCTYPE,
	ROUNDING_DOCUMENT,
	SETTINGS_DOCTYPE,
)
from neocharges.discounts import calc

COMPUTED_FIELDS = (ND_EFFECTIVE, ND_UNIT, ND_LINE)


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


def rate_precision(item, settings):
	"""Precision for gross, discount and net rate.

	Never finer than the rate field itself: ERPNext rounds every item float to
	its field precision, and a finer figure would not survive that.
	"""
	field_precision = cint(item.precision("rate"))
	if (settings.get("discount_rounding") or "") == ROUNDING_DOCUMENT:
		return field_precision or 2
	return min(2, field_precision) if field_precision else 2


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
	total = 0.0

	for item in doc.get("items") or []:
		if calc.has_input(flt(item.get(ND_DISC_1)), flt(item.get(ND_DISC_2)), flt(item.get(ND_EXTRA))):
			_apply_line(doc, item, settings)
			total += flt(item.get(ND_LINE))
		elif cint(item.get(ND_ACTIVE)):
			_release_line(item)

	if doc.meta.has_field(ND_TOTAL):
		doc.set(ND_TOTAL, flt(total, doc.precision(ND_TOTAL) or 2))

	from neocharges.discounts import zatca

	zatca.sync_document(doc, settings)


def _label(item):
	return _("Row {0} ({1})").format(item.idx, item.item_code or _("no item"))


def _apply_line(doc, item, settings):
	label = _label(item)

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
		# First application on a line with no price list: the rate typed in
		# is the gross rate.
		gross = flt(item.get("rate"))
	if gross <= 0:
		frappe.throw(_("{0}: set a Gross Rate before applying a discount.").format(label))

	precision = rate_precision(item, settings)

	try:
		result = calc.compute_line(
			gross,
			flt(item.get("qty")),
			flt(item.get(ND_DISC_1)),
			flt(item.get(ND_DISC_2)),
			flt(item.get(ND_EXTRA)),
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
	item.set(ND_UNIT, result.unit_discount)
	item.set(ND_EFFECTIVE, result.effective_pct)
	item.set(ND_LINE, result.line_discount)
	item.set(ND_ACTIVE, 1)

	_set_discount_account(doc, item, reason)


def _release_line(item):
	"""Discount inputs were cleared: restore the gross rate."""
	gross = flt(item.get(ND_GROSS))
	if gross > 0:
		item.price_list_rate = gross
		item.rate = gross
	item.discount_percentage = 0
	item.discount_amount = 0
	for field in COMPUTED_FIELDS:
		item.set(field, 0)
	item.set(ND_ACTIVE, 0)


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
		return _("{0}: set a Gross Rate before applying a discount.").format(label)
	if exc.code == calc.ERR_PERCENT:
		return _("{0}: discount percentages must be between 0 and 100.").format(label)
	if exc.code == calc.ERR_EXTRA:
		return _("{0}: the extra discount per unit cannot be negative.").format(label)
	if exc.code == calc.ERR_NEGATIVE:
		return _("{0}: the discounts add up to more than the gross rate.").format(label)
	if exc.code == calc.ERR_FULL:
		return _(
			"{0}: this discount brings the rate to zero. Enable 'Allow 100% Line Discount' in "
			"NeoCharges Settings if that is intended."
		).format(label)
	return _("{0}: invalid discount").format(label)


# ---------------------------------------------------------------------------
# Reasons, caps, accounts
# ---------------------------------------------------------------------------
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

	ERPNext validates `discount_percentage` against Item -> Max Discount. This
	app keeps that field at zero, so without this check the item master's cap
	would silently stop applying.
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
	"""Route the discount to the reason's account when ERPNext posts it.

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
	"""Fail loudly if anything between before_validate and now moved a rate.

	A pricing rule fetched during validate, or another app's hook, could
	rewrite `rate`. Posting a document whose rate no longer matches its
	recorded discount would put wrong figures on the ZATCA XML, so stop.
	"""
	if not is_discount_enabled(doc.doctype) or doc.get("is_consolidated"):
		return

	settings = get_settings()
	for item in doc.get("items") or []:
		if not cint(item.get(ND_ACTIVE)):
			continue
		precision = rate_precision(item, settings)
		tolerance = 0.5 / (10**precision)
		expected = flt(flt(item.get(ND_GROSS)) - flt(item.get(ND_UNIT)), precision)

		if abs(flt(item.rate) - expected) > tolerance or abs(
			flt(item.discount_amount) - flt(item.get(ND_UNIT))
		) > tolerance:
			frappe.throw(
				_(
					"{0}: the rate was changed to {1} after the item discount set it to {2}. "
					"Another rule or app is repricing this line. Remove the conflicting rule or the "
					"item discount, then save again."
				).format(_label(item), flt(item.rate), expected),
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


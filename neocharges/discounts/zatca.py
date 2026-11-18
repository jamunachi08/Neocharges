"""ZATCA compatibility for item discounts.

Keep Rate method
----------------
Rates are untouched, so each InvoiceLine prints the entered price and line
amount. The discount total reaches the XML as the invoice's document-level
AllowanceCharge, which ZATCA apps on ERPNext build from the document's
Additional Discount (`discount_amount`) and the invoice-level reason fields.
This module fills the reason fields, leaves the line-discount flag off, and
blocks two things ZATCA apps reject or mis-report: a nominal invoice, and a
document discount spread over more than one VAT category.

Reduce Rate method
------------------
ZATCA e-invoicing apps on ERPNext build each InvoiceLine's price block from
the native item fields:

    cac:Price/cbc:PriceAmount                    <- item.rate (net)
    cac:Price/cac:AllowanceCharge/cbc:Amount     <- item.discount_amount
    cac:Price/cac:AllowanceCharge/cbc:BaseAmount <- item.rate + item.discount_amount
    .../cbc:AllowanceChargeReasonCode / Reason   <- invoice-level reason fields

and only emit the AllowanceCharge block when the invoice's "submit line item
discount" flag is ticked. The engine already guarantees rate +
discount_amount == price_list_rate at two decimals, so this module only has
to:

  1. tick that flag on invoices that carry item discounts (and untick it if
     it was this app that ticked it and the discounts are gone),
  2. fill the invoice-level reason code and text from the line reasons,
  3. refuse combinations ZATCA rejects (nominal invoices with line discounts,
     a missing reason code),
  4. warn when tax-inclusive pricing will make the XML's own rounding drift.

Field names follow the common convention and can be overridden in Settings.
The XML itself is never touched here: it is built and signed by the ZATCA
app, and altering it after signing would break the hash.

For a ZATCA app that wants per-line reasons instead of one invoice-level
reason, `get_line_allowances(doc)` returns everything needed per line.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from neocharges.constants import (
	MIXED_REASON_BLOCK,
	MODE_KEEP,
	ND_ACTIVE,
	ND_LINE,
	ND_MODE,
	ND_REASON,
	ND_ZATCA_FLAG_SET,
	ND_ZATCA_REASON_SET,
	REASON_DOCTYPE,
	ZATCA_DOCTYPES,
	ZATCA_FLAG_FIELD,
	ZATCA_NOMINAL_FIELD,
	ZATCA_REASON_CODE_FIELD,
	ZATCA_REASON_FIELD,
)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def zatca_app_installed():
	from neocharges.zatca_check.vendors import get_profile, vendor_installed

	return vendor_installed(get_profile())


def field_map(settings):
	"""Invoice discount fields of the configured ZATCA vendor."""
	from neocharges.zatca_check.vendors import get_profile

	profile = get_profile(settings)
	return frappe._dict(
		flag=profile.f("line_flag") or ZATCA_FLAG_FIELD,
		code=profile.f("reason_code") or ZATCA_REASON_CODE_FIELD,
		reason=profile.f("reason") or ZATCA_REASON_FIELD,
	)


def is_active_for(doc, settings):
	if doc.doctype not in ZATCA_DOCTYPES:
		return False
	if not cint(settings.get("zatca_integration")):
		return False
	fields = field_map(settings)
	return zatca_app_installed() or doc.meta.has_field(fields.flag)


def reason_text(reason, settings):
	if (settings.get("zatca_reason_language") or "English") == "Arabic" and reason.get("reason_text_ar"):
		return reason.reason_text_ar
	return reason.reason_text_en or reason.reason_name


# ---------------------------------------------------------------------------
# Document sync (called from the engine's before_validate)
# ---------------------------------------------------------------------------
def sync_document(doc, settings):
	if not is_active_for(doc, settings):
		return

	fields = field_map(settings)
	active = [item for item in doc.get("items") or [] if cint(item.get(ND_ACTIVE))]

	if not active:
		_release_document(doc, fields)
		return

	if cint(doc.get(ZATCA_NOMINAL_FIELD)):
		frappe.throw(
			_(
				"This is a ZATCA nominal invoice. ZATCA does not accept line item discounts on "
				"nominal invoices. Clear the item discount fields, or untick the nominal invoice option."
			),
			title=_("ZATCA"),
		)

	reason = pick_reason(active, settings)
	if not reason:
		frappe.throw(
			_(
				"ZATCA requires a discount reason code on every allowance. Set a Discount Reason on the "
				"discounted lines, or a Default Discount Reason in NeoCharges Settings."
			),
			title=_("ZATCA"),
		)

	keep_rate = all(item.get(ND_MODE) == MODE_KEEP for item in active)

	if keep_rate:
		_guard_vat_categories(doc)
		# The discount travels as a document-level allowance; the line flag
		# would only add zero-value price allowances.
		if cint(doc.get(ND_ZATCA_FLAG_SET)) and doc.meta.has_field(fields.flag):
			doc.set(fields.flag, 0)
			_set_tracker(doc, ND_ZATCA_FLAG_SET, 0)
	elif doc.meta.has_field(fields.flag):
		if not cint(doc.get(fields.flag)):
			doc.set(fields.flag, 1)
			_set_tracker(doc, ND_ZATCA_FLAG_SET, 1)
	elif doc.doctype == "POS Invoice":
		_warn_pos_company_flag(doc, fields)

	if doc.meta.has_field(fields.code):
		owned = cint(doc.get(ND_ZATCA_REASON_SET))
		if owned or not doc.get(fields.code):
			doc.set(fields.code, reason.reason_code)
			if doc.meta.has_field(fields.reason):
				doc.set(fields.reason, reason_text(reason, settings))
			_set_tracker(doc, ND_ZATCA_REASON_SET, 1)

	if not keep_rate:
		_warn_inclusive_rounding(doc)


def _guard_vat_categories(doc):
	"""A document discount must sit in one VAT category.

	ZATCA apps on ERPNext deduct the whole document discount from the first
	VAT category and refuse invoices that combine a document discount with
	several categories. Distinct item tax templates are the signal.
	"""
	templates = {item.get("item_tax_template") or "" for item in doc.get("items") or []}
	if len(templates) <= 1:
		return
	frappe.throw(
		_(
			"This invoice mixes VAT categories ({0}). With the 'Keep Rate' method the item discounts "
			"reach ZATCA as one document-level discount, which ZATCA apps can only place in a single "
			"VAT category. Split the invoice by VAT category, or use the 'Reduce Rate' method."
		).format(", ".join(sorted(t or _("no template") for t in templates))),
		title=_("ZATCA"),
	)


def _set_tracker(doc, fieldname, value):
	if doc.meta.has_field(fieldname):
		doc.set(fieldname, value)


def _release_document(doc, fields):
	"""Undo only what this app set. A flag the user ticked stays ticked."""
	if cint(doc.get(ND_ZATCA_FLAG_SET)):
		if doc.meta.has_field(fields.flag):
			doc.set(fields.flag, 0)
		_set_tracker(doc, ND_ZATCA_FLAG_SET, 0)

	if cint(doc.get(ND_ZATCA_REASON_SET)):
		for fieldname in (fields.code, fields.reason):
			if doc.meta.has_field(fieldname):
				doc.set(fieldname, None)
		_set_tracker(doc, ND_ZATCA_REASON_SET, 0)


def pick_reason(active_items, settings):
	"""One reason for the invoice, chosen from the line reasons.

	The invoice-level field convention carries a single reason code. Lines
	sharing a code and text count as one reason. With several, either the
	reason carrying the largest discount wins or saving is blocked,
	depending on Settings.
	"""
	weight = {}
	docs = {}
	for item in active_items:
		name = item.get(ND_REASON)
		if not name:
			continue
		reason = frappe.get_cached_doc(REASON_DOCTYPE, name)
		key = (reason.reason_code, reason_text(reason, settings))
		weight[key] = weight.get(key, 0.0) + abs(flt(item.get(ND_LINE)))
		docs.setdefault(key, reason)

	if not weight:
		default = settings.get("default_discount_reason")
		return frappe.get_cached_doc(REASON_DOCTYPE, default) if default else None

	if len(weight) > 1 and (settings.get("zatca_mixed_reasons") or "") == MIXED_REASON_BLOCK:
		listed = ", ".join(f"{code} ({text})" for code, text in weight)
		frappe.throw(
			_(
				"This invoice mixes discount reasons {0}. The ZATCA invoice carries a single reason "
				"code for line discounts. Use one reason, or split the invoice."
			).format(listed),
			title=_("ZATCA"),
		)

	best = max(weight, key=lambda key: weight[key])
	return docs[best]


def _warn_pos_company_flag(doc, fields):
	if not frappe.get_meta("Company").has_field(fields.flag):
		return
	if cint(frappe.get_cached_value("Company", doc.company, fields.flag)):
		return
	frappe.msgprint(
		_(
			"Item discounts on POS Invoices are reported to ZATCA only when '{0}' is ticked on "
			"Company {1}. Until then the discounted net price is reported without the discount detail."
		).format(frappe.get_meta("Company").get_label(fields.flag), doc.company),
		indicator="orange",
		alert=True,
	)


# ---------------------------------------------------------------------------
# Line allowances: preview and integration API
# ---------------------------------------------------------------------------
def _vat_context(doc):
	taxes = doc.get("taxes") or []
	if not taxes:
		return False, 0.0
	first = taxes[0]
	return bool(cint(first.get("included_in_print_rate"))), flt(first.get("rate"))


def get_line_allowances(doc, settings=None):
	"""Per-line figures as a ZATCA builder would print them.

	Works for any discounted line, whether the discount came from this app or
	was typed into ERPNext's own discount fields. Amounts are VAT-exclusive,
	two decimals, in document currency. `checks_ok` is False where the
	printed figures would not reconcile.
	"""
	if settings is None:
		from neocharges.discounts.engine import get_settings

		settings = get_settings()

	fields = field_map(settings)
	inclusive, vat_rate = _vat_context(doc)
	divisor = 1 + vat_rate / 100.0 if inclusive else 1.0

	doc_code = doc.get(fields.code) if doc.meta.has_field(fields.code) else None
	doc_reason = doc.get(fields.reason) if doc.meta.has_field(fields.reason) else None

	lines = []
	for item in doc.get("items") or []:
		discount = abs(flt(item.discount_amount))
		net = abs(flt(item.rate))
		qty = flt(item.qty)

		price_amount = round(net / divisor, 2)
		allowance = round(discount / divisor, 2)
		base_amount = round((net + discount) / divisor, 2)

		line_code, line_text = doc_code, doc_reason
		if item.get(ND_REASON):
			reason = frappe.get_cached_doc(REASON_DOCTYPE, item.get(ND_REASON))
			line_code, line_text = reason.reason_code, reason_text(reason, settings)

		checks = []
		if discount and abs(round(price_amount + allowance, 2) - base_amount) > 0.001:
			checks.append(_("price + allowance does not equal base amount after VAT-exclusive rounding"))
		if not inclusive and abs(round(net * abs(qty), 2) - round(abs(flt(item.amount)), 2)) > 0.011:
			checks.append(_("quantity x price does not equal the line amount"))
		if discount and not line_code:
			checks.append(_("no reason code"))

		lines.append(
			{
				"idx": item.idx,
				"item_code": item.item_code,
				"qty": qty,
				"price_amount": price_amount,
				"allowance_amount": allowance,
				"base_amount": base_amount,
				"reason_code": line_code if discount else None,
				"reason": line_text if discount else None,
				"line_extension_amount": round(qty * price_amount, 2),
				"from_item_discount": bool(cint(item.get(ND_ACTIVE))),
				"line_item_discount": round(abs(flt(item.get("distributed_discount_amount"))), 2)
				if item.get(ND_MODE) == MODE_KEEP
				else 0.0,
				"checks": checks,
				"checks_ok": not checks,
			}
		)

	document_allowance = None
	keep_lines = [item for item in doc.get("items") or [] if item.get(ND_MODE) == MODE_KEEP and cint(item.get(ND_ACTIVE))]
	if keep_lines or flt(doc.get("discount_amount")):
		distributed = sum(flt(item.get("distributed_discount_amount")) for item in doc.get("items") or [])
		amount = abs(flt(doc.get("discount_amount")))
		document_allowance = {
			"amount": round(amount, 2),
			"reason_code": doc_code,
			"reason": doc_reason,
			"from_item_discounts": bool(keep_lines),
			"checks_ok": not keep_lines or abs(round(abs(distributed), 2) - round(amount, 2)) < 0.011,
		}

	return {
		"document_allowance": document_allowance,
		"tax_inclusive": inclusive,
		"vat_rate": vat_rate,
		"currency": doc.get("currency"),
		"flag_field": fields.flag,
		"flag_set": bool(cint(doc.get(fields.flag))) if doc.meta.has_field(fields.flag) else None,
		"lines": lines,
	}


def _warn_inclusive_rounding(doc):
	inclusive, _rate = _vat_context(doc)
	if not inclusive:
		return
	result = get_line_allowances(doc)
	drifting = [line for line in result["lines"] if line["allowance_amount"] and not line["checks_ok"]]
	if not drifting:
		return
	rows = ", ".join(str(line["idx"]) for line in drifting)
	frappe.msgprint(
		_(
			"Rows {0}: with VAT-inclusive pricing, the VAT-exclusive price, discount and base amount "
			"each round separately on the ZATCA XML and miss each other by 0.01. Adjust the gross "
			"rate or discount slightly, or price these lines VAT-exclusive. Use 'ZATCA Discount "
			"Preview' to check."
		).format(rows),
		title=_("ZATCA rounding"),
		indicator="orange",
	)

"""VAT on charge lines.

Treatment, on each line:

    No VAT          the amount is the whole charge
    VAT Exclusive   the amount is net; VAT is added on top
    VAT Inclusive   the amount already contains VAT; it is split out

Direction:

    Input VAT   a charge the company pays (freight, clearing): VAT is
                recoverable, debited to the input VAT account
    Output VAT  a charge the company bills (delivery to a customer): VAT is
                payable, credited to the output VAT account

Every line stores, in its own currency, net_amount + vat_amount =
gross_amount, and in company currency base_net_amount + base_vat_amount =
base_amount, exactly. The company-currency net is derived as gross minus
VAT rather than rounded on its own, so each posting balances to the halala.

`split_vat` is pure arithmetic and can be tested without a site.
"""

import frappe
from frappe import _
from frappe.utils import flt

from neocharges.constants import (
	PURCHASE_DOCTYPES,
	VAT_EXCLUSIVE,
	VAT_INCLUSIVE,
	VAT_INPUT,
	VAT_NONE,
	VAT_OUTPUT,
)


def split_vat(amount, rate, treatment, precision=2, rnd=None):
	"""(net, vat, gross) in the amount's own currency."""
	rnd = rnd or (lambda value, p: round(value + 0.0, p))
	amount = float(amount or 0)
	rate = float(rate or 0)

	if treatment == VAT_INCLUSIVE and rate:
		gross = rnd(amount, precision)
		net = rnd(gross / (1.0 + rate / 100.0), precision)
		return net, rnd(gross - net, precision), gross

	if treatment == VAT_EXCLUSIVE and rate:
		net = rnd(amount, precision)
		vat = rnd(net * rate / 100.0, precision)
		return net, vat, rnd(net + vat, precision)

	value = rnd(amount, precision)
	return value, 0.0, value


def base_split(gross, vat, exchange_rate, precision=2, rnd=None):
	"""(base_net, base_vat, base_gross) in company currency, net = gross - vat."""
	rnd = rnd or (lambda value, p: round(value + 0.0, p))
	base_gross = rnd(float(gross) * float(exchange_rate), precision)
	base_vat = rnd(float(vat) * float(exchange_rate), precision)
	return rnd(base_gross - base_vat, precision), base_vat, base_gross


def has_vat(row):
	return (row.get("vat_treatment") or VAT_NONE) != VAT_NONE and flt(row.get("vat_rate")) > 0


def default_direction(doc):
	return VAT_INPUT if doc.doctype in PURCHASE_DOCTYPES else VAT_OUTPUT


def default_rate(settings):
	value = settings.get("default_vat_rate")
	return 15.0 if value is None else flt(value)


def vat_account_for(company, direction, settings):
	for row in settings.get("charge_vat_accounts") or []:
		if row.company == company:
			return row.input_vat_account if direction == VAT_INPUT else row.output_vat_account
	return None


def resolve_line_vat(doc, row, settings, precision=2):
	"""Fill the VAT fields and company-currency split on one line.

	Must run after the line's currency and exchange rate are resolved.
	"""
	treatment = row.get("vat_treatment") or VAT_NONE
	row.vat_treatment = treatment

	if treatment == VAT_NONE:
		row.vat_rate = 0
		row.vat_account = None
	else:
		# A VAT treatment with no rate means the default rate. A zero-rated
		# charge is "No VAT" (or, on an invoice line, an Item Tax Template).
		if not flt(row.get("vat_rate")):
			row.vat_rate = default_rate(settings)
		if not row.get("vat_direction"):
			row.vat_direction = default_direction(doc)

	net, vat, gross = split_vat(row.amount, row.get("vat_rate"), treatment, precision, flt)
	row.net_amount, row.vat_amount, row.gross_amount = net, vat, gross

	base_net, base_vat, base_gross = base_split(gross, vat, flt(row.exchange_rate) or 1.0, precision, flt)
	row.base_net_amount, row.base_vat_amount, row.base_amount = base_net, base_vat, base_gross


def resolve_vat_account(doc, row, settings):
	"""The VAT account for a line that posts its own VAT (Journal Entry, inline, Add To)."""
	if not has_vat(row) or flt(row.get("vat_amount")) == 0:
		row.vat_account = None
		return
	if row.get("vat_account"):
		return
	account = vat_account_for(doc.company, row.vat_direction or default_direction(doc), settings)
	if not account:
		frappe.throw(
			_(
				"Charge row {0}: no {1} account is set for company {2}. Set it on the line, or add it "
				"under NeoCharges Settings → Charge VAT Accounts."
			).format(row.idx, _(row.vat_direction or default_direction(doc)), doc.company)
		)
	row.vat_account = account


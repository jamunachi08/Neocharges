"""Charge-line currencies.

Each charge line has its own currency:

    amount         in the line's currency (what the supplier's bill says)
    exchange_rate  line currency -> company currency
    base_amount    company currency, the value that posts

Rates, in order of precedence:
  line in company currency   1
  line in document currency  the document's own exchange rate, always, so a
                             USD freight line on a USD invoice moves with the
                             invoice's rate
  any other currency         the rate the user entered, else ERPNext's rate
                             for the posting date

Percentage lines are computed on the document's totals and are always in
document currency.

Pure arithmetic lives in `split_for_account` and `to_document_currency` so it
can be reasoned about without a site.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from neocharges.constants import CHARGE_BASIS_PERCENTAGE

RATE_PRECISION = 9


def company_currency(doc):
	return frappe.get_cached_value("Company", doc.company, "default_currency")


def document_currency(doc):
	return doc.get("currency") or company_currency(doc)


def document_rate(doc):
	"""Document currency -> company currency."""
	if document_currency(doc) == company_currency(doc):
		return 1.0
	return flt(doc.get("conversion_rate")) or 1.0


def posting_date(doc):
	return doc.get("posting_date") or doc.get("transaction_date") or nowdate()


def lookup_rate(from_currency, to_currency, date):
	if from_currency == to_currency:
		return 1.0
	try:
		from erpnext.setup.utils import get_exchange_rate

		return flt(get_exchange_rate(from_currency, to_currency, date)) or 0.0
	except Exception:  # noqa: BLE001 — rate lookup must never block validation
		frappe.log_error(
			title="NeoCharges: exchange rate lookup failed",
			message=frappe.get_traceback(),
		)
		return 0.0


def amount_precision(doc):
	return doc.precision("base_net_total") if doc.meta.has_field("base_net_total") else 2


def resolve_line_currency(doc, row):
	"""Fill charge_currency, exchange_rate and base_amount on one line."""
	company = company_currency(doc)
	document = document_currency(doc)

	if row.charge_basis == CHARGE_BASIS_PERCENTAGE:
		row.charge_currency = document
	elif not row.get("charge_currency"):
		row.charge_currency = company

	if row.charge_currency == company:
		row.exchange_rate = 1.0
	elif row.charge_currency == document:
		row.exchange_rate = document_rate(doc)
	elif flt(row.get("exchange_rate")) <= 0:
		rate = lookup_rate(row.charge_currency, company, posting_date(doc))
		if not rate:
			frappe.throw(
				_(
					"Charge row {0}: no exchange rate from {1} to {2} for {3}. Enter the rate on the "
					"row, or add a Currency Exchange record."
				).format(row.idx, row.charge_currency, company, posting_date(doc))
			)
		row.exchange_rate = rate

	row.exchange_rate = flt(row.exchange_rate, RATE_PRECISION)
	row.base_amount = flt(flt(row.amount) * row.exchange_rate, amount_precision(doc))


def to_document_currency(row, document, doc_rate, precision):
	"""The line's value in document currency, for the native taxes table.

	Exact when the line is already in document currency; otherwise converted
	through company currency with the document's rate, which is the only rate
	ERPNext applies to a taxes row.
	"""
	if row.charge_currency == document:
		return flt(row.amount, precision)
	return flt(flt(row.base_amount) / (doc_rate or 1.0), precision)


def split_for_account(base_amount, line_amount, line_currency, line_rate, account_currency,
                      company, document, doc_rate, fallback_rate):
	"""Company and account-currency amounts for one ledger side.

	Returns (account_amount, exchange_rate). The company-currency amount is
	always `base_amount`. The rate returned makes account_amount x rate
	round back to `base_amount`, so a multi-currency Journal Entry balances
	to the cent whatever currencies its sides are in.
	"""
	if account_currency == company:
		return base_amount, 1.0
	if account_currency == line_currency:
		return line_amount, line_rate

	rate = doc_rate if account_currency == document else fallback_rate
	if not rate:
		return base_amount, 1.0

	account_amount = flt(base_amount / rate, 2)
	exact_rate = flt(base_amount / account_amount, RATE_PRECISION) if account_amount else rate
	return account_amount, exact_rate

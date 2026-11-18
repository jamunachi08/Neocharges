"""Default charge accounts from the source document.

A charge line (Add To: None) with a blank account takes it from the document:

    Selling documents   debit  -> the customer's receivable (party: customer)
                        credit -> revenue
    Buying documents    debit  -> expense
                        credit -> the supplier's payable (party: supplier)

So a template line with both accounts blank bills the customer as extra
revenue, or books an extra expense owed to the supplier; a line with only one
account set uses the document for the other side.

Where each account comes from:

    receivable   Sales Invoice "Debit To", else Company default receivable
    payable      Purchase Invoice "Credit To", else Company default payable
    revenue      the items' income account when they all share one, else
                 Company default income account
    expense      the items' expense account when they all share one and none
                 is a stock or fixed-asset item (those carry stock or asset
                 clearing accounts, not expenses), else Company default
                 expense account

A side filled this way is flagged on the line (Debit / Credit from document)
and re-resolved on every save, so it follows a change of customer, supplier
or items. Choosing an account on the line clears the flag and the choice
sticks.
"""

import frappe
from frappe import _

from neocharges.constants import CHARGE_TABLE_FIELD, PURCHASE_DOCTYPES, SALES_DOCTYPES

DEBIT_FLAG = "debit_from_document"
CREDIT_FLAG = "credit_from_document"


class DocumentAccount:
	__slots__ = ("account", "party_type", "party", "source")

	def __init__(self, account, party_type=None, party=None, source=""):
		self.account = account
		self.party_type = party_type
		self.party = party
		self.source = source


def _company_value(doc, fieldname):
	return frappe.get_cached_value("Company", doc.company, fieldname)


def _single_item_account(doc, fieldname, skip=None):
	accounts = set()
	for item in doc.get("items") or []:
		if skip and skip(item):
			return None
		value = item.get(fieldname)
		if not value:
			return None
		accounts.add(value)
	return accounts.pop() if len(accounts) == 1 else None


def _is_stock_or_asset(item):
	if item.get("is_fixed_asset"):
		return True
	if not item.get("item_code"):
		return False
	return bool(frappe.get_cached_value("Item", item.item_code, "is_stock_item"))


def _customer(doc):
	if doc.doctype == "Quotation":
		if (doc.get("quotation_to") or "Customer") != "Customer":
			return None
		return doc.get("party_name")
	return doc.get("customer")


def receivable(doc):
	party = _customer(doc)
	account = doc.get("debit_to") if doc.meta.has_field("debit_to") else None
	source = _("the invoice's Debit To") if account else _("the company's default receivable account")
	account = account or _company_value(doc, "default_receivable_account")
	return DocumentAccount(account, "Customer", party, source)


def payable(doc):
	party = doc.get("supplier")
	account = doc.get("credit_to") if doc.meta.has_field("credit_to") else None
	source = _("the invoice's Credit To") if account else _("the company's default payable account")
	account = account or _company_value(doc, "default_payable_account")
	return DocumentAccount(account, "Supplier", party, source)


def revenue(doc):
	account = _single_item_account(doc, "income_account")
	if account:
		return DocumentAccount(account, source=_("the items' income account"))
	return DocumentAccount(
		_company_value(doc, "default_income_account"),
		source=_("the company's default income account"),
	)


def expense(doc):
	account = _single_item_account(doc, "expense_account", skip=_is_stock_or_asset)
	if account:
		return DocumentAccount(account, source=_("the items' expense account"))
	return DocumentAccount(
		_company_value(doc, "default_expense_account"),
		source=_("the company's default expense account"),
	)


def document_side(doc, side):
	"""The document's account for 'debit' or 'credit', or None if not applicable."""
	if doc.doctype in SALES_DOCTYPES:
		return receivable(doc) if side == "debit" else revenue(doc)
	if doc.doctype in PURCHASE_DOCTYPES:
		return expense(doc) if side == "debit" else payable(doc)
	return None


def _previous_rows(doc):
	getter = getattr(doc, "get_doc_before_save", None)
	before = getter() if callable(getter) else None
	if not before:
		return {}
	return {row.name: row for row in before.get(CHARGE_TABLE_FIELD) or []}


def apply_document_accounts(doc, rows):
	"""Fill blank (or document-flagged) accounts on memo rows from the document."""
	previous = _previous_rows(doc)

	for row in rows:
		before = previous.get(row.name)
		for side, field, flag in (
			("debit", "debit_account", DEBIT_FLAG),
			("credit", "credit_account", CREDIT_FLAG),
		):
			flagged = bool(row.get(flag))
			# An account changed by hand since the last save is the user's choice.
			if flagged and before is not None and before.get(field) != row.get(field) and row.get(field):
				flagged = False

			if row.get(field) and not flagged:
				row.set(flag, 0)
				continue

			resolved = document_side(doc, side)
			if not resolved or not resolved.account:
				what = _("debit") if side == "debit" else _("credit")
				frappe.throw(
					_(
						"Charge row {0}: no {1} account is set, and the document has none to use ({2}). "
						"Choose the account on the line or the template, or set it on the Company."
					).format(row.idx, what, resolved.source if resolved else _(doc.doctype))
				)

			row.set(field, resolved.account)
			row.set(flag, 1)

			if resolved.party_type:
				if not resolved.party:
					frappe.throw(
						_(
							"Charge row {0}: the {1} side uses {2}, which needs a {3}, but this document "
							"has none."
						).format(row.idx, side, resolved.account, _(resolved.party_type))
					)
				row.party_type = resolved.party_type
				row.party = resolved.party
				row.party_side = "Debit" if side == "debit" else "Credit"

"""Document default accounts for charge lines with blank accounts.

Run from the repo root:  python3 -m unittest neocharges.tests.test_document_accounts
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_frappe as ff  # noqa: E402

INSTALLED = ff.install()
if INSTALLED:
	from neocharges.charges import document_accounts as DA

skip_under_bench = unittest.skipUnless(INSTALLED, "uses a Frappe stand-in; run outside bench")
E = ff.Doc
S = ff.STORE


def setup():
	S.docs.clear()
	S.add(E("Company", name="Neo", default_receivable_account="Debtors - N",
	        default_payable_account="Creditors - N", default_income_account="Sales - N",
	        default_expense_account="Expenses - N"))
	S.add(E("Item", name="CAM", is_stock_item=1))
	S.add(E("Item", name="SUB", is_stock_item=0))
	S.add(E("Item", name="SVC", is_stock_item=0))


def sales_invoice(items=None, **values):
	base = {"name": "SINV-1", "company": "Neo", "customer": "Shiyad", "debit_to": "Debtors USD - N",
	        "items": items or [E("Sales Invoice Item", item_code="SUB", income_account="Subscriptions - N")]}
	base.update(values)
	return E("Sales Invoice", fields=("debit_to",), **base)


def purchase_invoice(items=None, **values):
	base = {"name": "PINV-1", "company": "Neo", "supplier": "Forwarder", "credit_to": "Creditors USD - N",
	        "items": items or [E("Purchase Invoice Item", item_code="SVC", expense_account="Freight - N")]}
	base.update(values)
	return E("Purchase Invoice", fields=("credit_to",), **base)


def line(idx=1, **values):
	base = {"idx": idx, "name": f"row{idx}", "debit_account": None, "credit_account": None,
	        "debit_from_document": 0, "credit_from_document": 0,
	        "party_type": None, "party": None, "party_side": None}
	base.update(values)
	return E("NeoCharge Line", **base)


@skip_under_bench
class TestSelling(unittest.TestCase):
	def setUp(self):
		setup()

	def test_both_blank(self):
		row = line()
		DA.apply_document_accounts(sales_invoice(), [row])
		self.assertEqual(row.debit_account, "Debtors USD - N")
		self.assertEqual(row.credit_account, "Subscriptions - N")
		self.assertEqual((row.party_type, row.party, row.party_side), ("Customer", "Shiyad", "Debit"))
		self.assertEqual((row.debit_from_document, row.credit_from_document), (1, 1))

	def test_debit_only_set_credits_revenue(self):
		row = line(debit_account="Freight Recovery - N")
		DA.apply_document_accounts(sales_invoice(), [row])
		self.assertEqual(row.credit_account, "Subscriptions - N")
		self.assertEqual((row.debit_from_document, row.credit_from_document), (0, 1))

	def test_credit_only_set_debits_receivable(self):
		row = line(credit_account="Delivery Income - N")
		DA.apply_document_accounts(sales_invoice(), [row])
		self.assertEqual(row.debit_account, "Debtors USD - N")
		self.assertEqual(row.party, "Shiyad")

	def test_mixed_income_accounts_use_company_default(self):
		items = [E("Sales Invoice Item", item_code="SUB", income_account="Subscriptions - N"),
		         E("Sales Invoice Item", item_code="CAM", income_account="Camera Revenue - N")]
		row = line()
		DA.apply_document_accounts(sales_invoice(items), [row])
		self.assertEqual(row.credit_account, "Sales - N")

	def test_flagged_side_follows_customer_change(self):
		row = line()
		DA.apply_document_accounts(sales_invoice(), [row])
		DA.apply_document_accounts(sales_invoice(customer="Other", debit_to="Debtors - N"), [row])
		self.assertEqual((row.debit_account, row.party), ("Debtors - N", "Other"))

	def test_hand_chosen_account_sticks(self):
		row = line()
		doc = sales_invoice()
		DA.apply_document_accounts(doc, [row])
		saved = E("NeoCharge Line", **{k: v for k, v in row.items() if k != "doctype"})
		row.credit_account = "Other Income - N"
		doc.get_doc_before_save = lambda: E("Sales Invoice", custom_neocharges=[saved])
		DA.apply_document_accounts(doc, [row])
		self.assertEqual((row.credit_account, row.credit_from_document), ("Other Income - N", 0))

	def test_delivery_note_uses_company_receivable(self):
		doc = E("Delivery Note", name="DN-1", company="Neo", customer="Shiyad", items=[])
		row = line()
		DA.apply_document_accounts(doc, [row])
		self.assertEqual((row.debit_account, row.credit_account), ("Debtors - N", "Sales - N"))

	def test_quotation_to_lead_cannot_use_receivable(self):
		doc = E("Quotation", name="Q-1", company="Neo", quotation_to="Lead", party_name="L-1", items=[])
		with self.assertRaises(RuntimeError):
			DA.apply_document_accounts(doc, [line()])

	def test_missing_company_default(self):
		S.get("Company", "Neo")["default_income_account"] = None
		items = [E("Sales Invoice Item", item_code="SUB", income_account=None)]
		with self.assertRaises(RuntimeError):
			DA.apply_document_accounts(sales_invoice(items), [line()])


@skip_under_bench
class TestBuying(unittest.TestCase):
	def setUp(self):
		setup()

	def test_both_blank(self):
		row = line()
		DA.apply_document_accounts(purchase_invoice(), [row])
		self.assertEqual(row.debit_account, "Freight - N")
		self.assertEqual(row.credit_account, "Creditors USD - N")
		self.assertEqual((row.party_type, row.party, row.party_side), ("Supplier", "Forwarder", "Credit"))

	def test_stock_items_never_debit_their_clearing_account(self):
		items = [E("Purchase Invoice Item", item_code="CAM", expense_account="Stock Received But Not Billed - N")]
		row = line()
		DA.apply_document_accounts(purchase_invoice(items), [row])
		self.assertEqual(row.debit_account, "Expenses - N")

	def test_fixed_asset_items_use_company_expense(self):
		items = [E("Purchase Invoice Item", item_code="SVC", is_fixed_asset=1, expense_account="CWIP - N")]
		row = line()
		DA.apply_document_accounts(purchase_invoice(items), [row])
		self.assertEqual(row.debit_account, "Expenses - N")

	def test_purchase_order_uses_company_payable(self):
		doc = E("Purchase Order", name="PO-1", company="Neo", supplier="Forwarder", items=[])
		row = line(debit_account="Freight - N")
		DA.apply_document_accounts(doc, [row])
		self.assertEqual(row.credit_account, "Creditors - N")


if __name__ == "__main__":
	unittest.main()

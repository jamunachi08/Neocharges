"""VAT on charge lines: arithmetic, ledger legs, taxes table, invoice lines.

Run from the repo root:  python3 -m unittest neocharges.tests.test_charge_vat
"""

import os
import random
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_frappe as ff  # noqa: E402

INSTALLED = ff.install()
if INSTALLED:
	if "erpnext.setup.utils" not in sys.modules:
		utils = types.ModuleType("erpnext.setup.utils")
		utils.get_exchange_rate = lambda a, b, date=None: {("USD", "SAR"): 3.75}.get((a, b), 0)
		sys.modules.update({"erpnext": types.ModuleType("erpnext"),
		                    "erpnext.setup": types.ModuleType("erpnext.setup"),
		                    "erpnext.setup.utils": utils})
	from neocharges.charges import currency as C
	from neocharges.charges import vat as V
	from neocharges.charges.posting import ledger_legs

skip_under_bench = unittest.skipUnless(INSTALLED, "uses a Frappe stand-in; run outside bench")
E = ff.Doc
flt = lambda v, p=None: round(float(v or 0), p) if p is not None else float(v or 0)  # noqa: E731


def settings():
	return E("NeoCharges Settings", name="NeoCharges Settings", default_vat_rate=15,
	         charge_vat_accounts=[E("NeoCharge VAT Account", company="Neo",
	                                input_vat_account="VAT Input - N", output_vat_account="VAT Output - N")])


def doc(doctype="Purchase Invoice", currency="SAR", rate=1.0):
	ff.STORE.docs.clear()
	ff.STORE.add(E("Company", name="Neo", default_currency="SAR"))
	return E(doctype, name="D-1", company="Neo", currency=currency, conversion_rate=rate,
	         posting_date="2026-11-18", net_total=1000)


def line(**values):
	base = {"idx": 1, "name": "row1", "charge_basis": "Amount", "amount": 0, "charge_currency": None,
	        "exchange_rate": 0, "vat_treatment": "No VAT", "vat_rate": 0, "vat_direction": None,
	        "vat_account": None, "debit_account": "Freight - N", "credit_account": "Creditors - N"}
	base.update(values)
	return E("NeoCharge Line", **base)


def resolve(d, row):
	C.resolve_line_currency(d, row)
	V.resolve_line_vat(d, row, settings(), 2)
	V.resolve_vat_account(d, row, settings())
	return row


def balance(legs):
	return round(sum(l.base if l.side == "debit" else -l.base for l in legs), 2)


@skip_under_bench
class TestSplit(unittest.TestCase):
	def test_exclusive(self):
		self.assertEqual(V.split_vat(1000, 15, "VAT Exclusive"), (1000, 150, 1150))

	def test_inclusive(self):
		self.assertEqual(V.split_vat(1150, 15, "VAT Inclusive"), (1000, 150, 1150))

	def test_inclusive_awkward(self):
		net, vat, gross = V.split_vat(100, 15, "VAT Inclusive")
		self.assertEqual((net, vat, gross), (86.96, 13.04, 100))

	def test_no_vat(self):
		self.assertEqual(V.split_vat(500, 15, "No VAT"), (500, 0, 500))

	def test_invariant(self):
		rng = random.Random(25)
		for _ in range(20000):
			amount = round(rng.uniform(0.01, 999999), 2)
			for treatment in ("VAT Exclusive", "VAT Inclusive"):
				net, vat, gross = V.split_vat(amount, rng.choice((5, 15)), treatment)
				self.assertEqual(round(net + vat, 2), gross)


@skip_under_bench
class TestLines(unittest.TestCase):
	def test_blank_rate_uses_default_and_direction_from_document(self):
		row = resolve(doc(), line(amount=1000, vat_treatment="VAT Exclusive"))
		self.assertEqual((row.vat_rate, row.vat_direction, row.vat_account), (15, "Input VAT", "VAT Input - N"))
		self.assertEqual((row.net_amount, row.vat_amount, row.gross_amount, row.base_amount), (1000, 150, 1150, 1150))

	def test_selling_document_defaults_to_output(self):
		row = resolve(doc("Sales Invoice"), line(amount=100, vat_treatment="VAT Inclusive"))
		self.assertEqual((row.vat_direction, row.vat_account), ("Output VAT", "VAT Output - N"))

	def test_missing_vat_account_refused(self):
		d = doc()
		row = line(amount=100, vat_treatment="VAT Exclusive")
		C.resolve_line_currency(d, row)
		V.resolve_line_vat(d, row, settings(), 2)
		empty = E("NeoCharges Settings", name="x", default_vat_rate=15, charge_vat_accounts=[])
		with self.assertRaises(RuntimeError):
			V.resolve_vat_account(d, row, empty)

	def test_usd_line_company_split_balances(self):
		row = resolve(doc(currency="USD", rate=3.75), line(amount=333.33, charge_currency="USD",
		                                                     vat_treatment="VAT Exclusive"))
		self.assertEqual(round(row.base_net_amount + row.base_vat_amount, 2), row.base_amount)


@skip_under_bench
class TestLegs(unittest.TestCase):
	def test_input_vat_legs(self):
		row = resolve(doc(), line(amount=1000, vat_treatment="VAT Exclusive"))
		legs = ledger_legs(row)
		self.assertEqual([(l.side, l.account, l.base) for l in legs], [
			("debit", "Freight - N", 1000), ("debit", "VAT Input - N", 150), ("credit", "Creditors - N", 1150)])

	def test_output_vat_legs(self):
		row = resolve(doc("Sales Order"), line(amount=1150, vat_treatment="VAT Inclusive",
		                                        debit_account="Debtors - N", credit_account="Delivery Income - N"))
		legs = ledger_legs(row)
		self.assertEqual([(l.side, l.account, l.base) for l in legs], [
			("debit", "Debtors - N", 1150), ("credit", "Delivery Income - N", 1000), ("credit", "VAT Output - N", 150)])

	def test_no_vat_two_legs(self):
		self.assertEqual(len(ledger_legs(resolve(doc(), line(amount=500)))), 2)

	def test_legs_balance_in_any_currency(self):
		rng = random.Random(7)
		for _ in range(5000):
			fx = rng.choice((1.0, 3.75, 4.05))
			cur = {1.0: "SAR", 3.75: "USD", 4.05: "EUR"}[fx]
			d = doc(currency="USD", rate=3.75)
			row = line(amount=round(rng.uniform(0.01, 99999), 2), charge_currency=cur, exchange_rate=fx,
			           vat_treatment=rng.choice(("VAT Exclusive", "VAT Inclusive")))
			V.resolve_line_vat(d, row, settings(), 2)
			row.vat_direction = rng.choice(("Input VAT", "Output VAT"))
			row.vat_account = "VAT - N"
			self.assertEqual(balance(ledger_legs(row)), 0)

	def test_line_currency_leg_converts_back(self):
		for base, line_amount, fx in ((1243.62, 331.63, 3.75), (0.04, 0.01, 3.75), (86996.6, 23199.09, 3.75)):
			amount, rate = C.split_for_account(base, line_amount, "USD", fx, "USD", "SAR", "USD", 3.75, 0)
			self.assertEqual((amount, round(amount * rate, 2)), (line_amount, base))


@skip_under_bench
class TestTaxesTable(unittest.TestCase):
	def test_net_row_plus_vat_row(self):
		from neocharges.charges.native_taxes import _tax_values, _vat_tax_values

		d = doc()
		d.items = [E("Purchase Invoice Item", qty=1)]
		row = resolve(d, line(amount=1150, vat_treatment="VAT Inclusive", apply_to="Valuation and Total",
		                       account_head="Freight - N", debit_account=None, credit_account=None))
		self.assertEqual(_tax_values(d, row)["tax_amount"], 1000)
		vat_row = _vat_tax_values(d, row)
		self.assertEqual((vat_row["tax_amount"], vat_row["account_head"], vat_row["category"]),
		                 (150, "VAT Input - N", "Total"))

	def test_no_vat_row_without_vat(self):
		from neocharges.charges.native_taxes import _vat_tax_values

		row = resolve(doc(), line(amount=100, apply_to="Total", account_head="Freight - N"))
		self.assertIsNone(_vat_tax_values(doc(), row))


@skip_under_bench
class TestInvoiceLine(unittest.TestCase):
	def invoice(self, inclusive):
		d = doc("Sales Invoice")
		d.taxes = [E("Sales Taxes and Charges", rate=15, included_in_print_rate=int(inclusive))]
		d.items = []
		ff.STORE.add(E("Item", name="DELIVERY", stock_uom="Nos", is_stock_item=0))
		return d

	def values(self, d, **line_values):
		from neocharges.charges.invoice_lines import _row_values

		row = resolve(d, line(apply_to="Invoice Line", charge_item="DELIVERY",
		                       charge_description="Delivery", **line_values))
		return _row_values(d, row)

	def test_exclusive_charge_on_exclusive_invoice_is_net(self):
		self.assertEqual(self.values(self.invoice(False), amount=200, vat_treatment="VAT Exclusive")["rate"], 200)

	def test_inclusive_charge_on_exclusive_invoice_is_net(self):
		self.assertEqual(self.values(self.invoice(False), amount=230, vat_treatment="VAT Inclusive")["rate"], 200)

	def test_exclusive_charge_on_inclusive_invoice_is_gross(self):
		self.assertEqual(self.values(self.invoice(True), amount=200, vat_treatment="VAT Exclusive")["rate"], 230)

	def test_return_is_negative(self):
		d = self.invoice(False)
		d.is_return = 1
		self.assertEqual(self.values(d, amount=200, vat_treatment="VAT Exclusive")["qty"], -1)


if __name__ == "__main__":
	unittest.main()

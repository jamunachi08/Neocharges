"""Charge-line currency arithmetic: a SAR company buying in USD.

Run from the repo root:  python3 -m unittest neocharges.tests.test_charge_currency
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
	# Rate lookups go to ERPNext; give the tests a fixed table instead.
	RATES = {("EUR", "SAR"): 4.05, ("USD", "SAR"): 3.75, ("AED", "SAR"): 1.0211}
	erpnext = types.ModuleType("erpnext")
	setup = types.ModuleType("erpnext.setup")
	utils = types.ModuleType("erpnext.setup.utils")
	utils.get_exchange_rate = lambda a, b, date=None: RATES.get((a, b), 0)
	sys.modules.update({"erpnext": erpnext, "erpnext.setup": setup, "erpnext.setup.utils": utils})

	from neocharges.charges import currency as C

skip_under_bench = unittest.skipUnless(INSTALLED, "uses a Frappe stand-in; run outside bench")
E = ff.Doc


def invoice(currency="USD", rate=3.75):
	ff.STORE.docs.clear()
	ff.STORE.add(E("Company", name="Neo", default_currency="SAR"))
	return E("Purchase Invoice", name="PINV-1", company="Neo", currency=currency,
	         conversion_rate=rate, posting_date="2026-11-03", net_total=10000)


def line(idx=1, **values):
	base = {"idx": idx, "charge_basis": "Amount", "amount": 0, "charge_currency": None, "exchange_rate": 0}
	base.update(values)
	return E("NeoCharge Line", **base)


@skip_under_bench
class TestResolveLine(unittest.TestCase):
	def test_local_expense_on_usd_invoice(self):
		doc, row = invoice(), line(amount=500, charge_currency="SAR")
		C.resolve_line_currency(doc, row)
		self.assertEqual((row.exchange_rate, row.base_amount), (1.0, 500.0))

	def test_foreign_freight_follows_invoice_rate(self):
		doc, row = invoice(), line(amount=1000, charge_currency="USD", exchange_rate=3.70)
		C.resolve_line_currency(doc, row)
		self.assertEqual((row.exchange_rate, row.base_amount), (3.75, 3750.0))

	def test_third_currency_uses_lookup_then_keeps_user_rate(self):
		doc, row = invoice(), line(amount=200, charge_currency="EUR")
		C.resolve_line_currency(doc, row)
		self.assertEqual((row.exchange_rate, row.base_amount), (4.05, 810.0))
		row.exchange_rate = 4.10
		C.resolve_line_currency(doc, row)
		self.assertEqual(row.base_amount, 820.0)

	def test_blank_currency_means_company_currency(self):
		doc, row = invoice(), line(amount=75)
		C.resolve_line_currency(doc, row)
		self.assertEqual((row.charge_currency, row.base_amount), ("SAR", 75.0))

	def test_percentage_line_is_in_document_currency(self):
		doc, row = invoice(), line(charge_basis="Percentage", amount=200, charge_currency="SAR")
		C.resolve_line_currency(doc, row)
		self.assertEqual((row.charge_currency, row.base_amount), ("USD", 750.0))

	def test_missing_rate_is_refused(self):
		doc, row = invoice(), line(amount=10, charge_currency="JPY")
		with self.assertRaises(RuntimeError):
			C.resolve_line_currency(doc, row)


@skip_under_bench
class TestTaxesTable(unittest.TestCase):
	def test_usd_line_goes_in_exactly(self):
		row = line(amount=1000, charge_currency="USD", base_amount=3750)
		self.assertEqual(C.to_document_currency(row, "USD", 3.75, 2), 1000.0)

	def test_sar_line_is_converted_not_relabelled(self):
		"""The 2.2 bug: SAR 500 was written as USD 500 (SAR 1,875)."""
		row = line(amount=500, charge_currency="SAR", base_amount=500)
		self.assertEqual(C.to_document_currency(row, "USD", 3.75, 2), 133.33)


@skip_under_bench
class TestJournalSides(unittest.TestCase):
	def split(self, base, amount, line_currency, line_rate, account_currency, fallback=0.0):
		return C.split_for_account(base, amount, line_currency, line_rate, account_currency,
		                           "SAR", "USD", 3.75, fallback)

	def test_company_account(self):
		self.assertEqual(self.split(3750, 1000, "USD", 3.75, "SAR"), (3750, 1.0))

	def test_usd_supplier_gets_the_bill_amount(self):
		self.assertEqual(self.split(3750, 1000, "USD", 3.75, "USD"), (1000, 3.75))

	def test_sar_charge_to_usd_account_balances(self):
		amount, rate = self.split(811, 811, "SAR", 1.0, "USD")
		self.assertEqual(amount, 216.27)
		self.assertEqual(round(amount * rate, 2), 811.0)

	def test_every_side_converts_back_to_base(self):
		rng = random.Random(23)
		for _ in range(20000):
			base = round(rng.uniform(0.01, 500000), 2)
			for account_currency, fallback in (("USD", 0), ("EUR", 4.05), ("AED", 1.0211)):
				amount, rate = self.split(base, base, "SAR", 1.0, account_currency, fallback)
				self.assertEqual(round(amount * rate, 2), base, (base, account_currency))


if __name__ == "__main__":
	unittest.main()

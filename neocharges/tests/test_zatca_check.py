"""Rule tests for the ZATCA pre-submit check, using fake_frappe.

Run from the repo root:  python3 -m unittest neocharges.tests.test_zatca_check
"""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_frappe as ff  # noqa: E402

INSTALLED = ff.install()
if INSTALLED:
	from neocharges.zatca_check import checks, vendors
skip_under_bench = unittest.skipUnless(INSTALLED, "uses a Frappe stand-in; run outside bench")

E = ff.Doc
S = ff.STORE

ERPGULF_INVOICE_FIELDS = (
	"custom_submit_line_item_discount_to_zatca",
	"custom_zatca_discount_reason_code",
	"custom_zatca_discount_reason",
	"custom_zatca_nominal_invoice",
	"custom_zatca_pmm",
	"custom_zatca_status",
	"custom_return_against_for_zatca",
	"custom_exemption_reason_code",
	"custom_tax_exemption_reason",
)


def settings(**values):
	base = {"zatca_vendor": "Auto-detect", "zatca_check_enabled": 1, "zatca_integration": 1}
	base.update(values)
	return E("NeoCharges Settings", name="NeoCharges Settings", **base)


def address(name, company=None, **values):
	base = {
		"address_line1": "King Fahd Road",
		"address_line2": "Al Olaya",
		"city": "Riyadh",
		"country": "Saudi Arabia",
		"pincode": "12211",
		"custom_building_number": "1234",
		"is_your_company_address": 1 if company else 0,
		"links": [E("Dynamic Link", link_doctype="Company", link_name=company)] if company else [],
	}
	base.update(values)
	return S.add(E("Address", name=name, **base))


def item(idx, **values):
	base = {"idx": idx, "item_code": f"ITEM-{idx}", "qty": 1, "rate": 100, "amount": 100,
	        "discount_amount": 0, "item_tax_template": None}
	base.update(values)
	return E("Sales Invoice Item", **base)


def invoice(**values):
	base = {
		"name": "SINV-1",
		"company": "Neo",
		"customer": "Acme",
		"posting_date": datetime.date.today().isoformat(),
		"currency": "SAR",
		"conversion_rate": 1,
		"items": [item(1)],
		"taxes": [E("Sales Taxes and Charges", rate=15, tax_amount=15, included_in_print_rate=0)],
		"discount_amount": 0,
		"apply_discount_on": "Grand Total",
		"grand_total": 115,
		"customer_address": "ACME-ADDR",
		"is_return": 0,
		"docstatus": 0,
	}
	base.update(values)
	return E("Sales Invoice", fields=ERPGULF_INVOICE_FIELDS, **base)


def reset(erpgulf=True):
	S.docs.clear()
	S.apps = ["frappe", "erpnext"] + (["zatca_erpgulf"] if erpgulf else [])
	S.metas = {
		"Sales Invoice": ff.Meta(ERPGULF_INVOICE_FIELDS if erpgulf else ()),
		"Customer": ff.Meta(("custom_b2c",) if erpgulf else ()),
	}
	company_fields = ("custom_zatca_invoice_enabled", "custom_phase_1_or_2", "custom_costcenter") if erpgulf else ()
	S.add(E("Company", fields=company_fields, name="Neo", country="Saudi Arabia",
	        tax_id="300000000000003", custom_zatca_invoice_enabled=1, custom_phase_1_or_2="Phase-2",
	        custom_costcenter=0))
	address("NEO-ADDR", company="Neo")
	address("ACME-ADDR", company=None)
	customer_fields = ("custom_b2c", "custom_buyer_id", "custom_buyer_id_type") if erpgulf else ()
	S.add(E("Customer", fields=customer_fields, name="Acme", tax_id="311111111111113",
	        custom_b2c=0, custom_buyer_id="1010101010", custom_buyer_id_type="CRN",
	        customer_primary_address="ACME-ADDR"))


def run(doc, **settings_values):
	return checks.run_checks(doc, settings(**settings_values))


def messages(result, level=None):
	return [f["message"] for f in result["findings"] if level is None or f["level"] == level]


@skip_under_bench
class TestVendorDetection(unittest.TestCase):
	def test_erpgulf_detected(self):
		reset()
		self.assertEqual(vendors.get_profile(settings()).key, vendors.VENDOR_ERPGULF)

	def test_fork_with_same_fields_detected(self):
		reset()
		S.apps = ["frappe", "erpnext", "neozatca"]
		self.assertEqual(vendors.get_profile(settings()).key, vendors.VENDOR_ERPGULF)

	def test_no_app(self):
		reset(erpgulf=False)
		self.assertEqual(vendors.get_profile(settings()).key, vendors.VENDOR_NONE)

	def test_explicit_vendor_wins(self):
		reset()
		self.assertEqual(vendors.get_profile(settings(zatca_vendor="Other")).key, vendors.VENDOR_OTHER)


@skip_under_bench
class TestChecks(unittest.TestCase):
	def setUp(self):
		reset()

	def test_clean_invoice_passes(self):
		result = run(invoice())
		self.assertEqual(result["status"], checks.STATUS_PASSED, messages(result))

	def test_company_tax_id_format(self):
		S.get("Company", "Neo")["tax_id"] = "12345"
		result = run(invoice())
		self.assertEqual(result["status"], checks.STATUS_FAILED)
		self.assertTrue(any("Tax ID" in m for m in messages(result, "error")))

	def test_building_number_and_postcode(self):
		S.get("Address", "NEO-ADDR").update(custom_building_number="12", pincode="1234")
		errors = messages(run(invoice()), "error")
		self.assertTrue(any("Building Number" in m for m in errors))
		self.assertTrue(any("Postal Code" in m for m in errors))

	def test_seller_address_from_other_company_warns(self):
		S.docs.clear()
		reset()
		del S.docs[("Address", "NEO-ADDR")]
		address("OTHER-ADDR", company="Other Co")
		warnings = messages(run(invoice()), "warning")
		self.assertTrue(any("belongs to Other Co" in m for m in warnings))

	def test_b2b_without_buyer_id(self):
		S.get("Customer", "Acme")["custom_buyer_id"] = ""
		self.assertTrue(any("Buyer ID" in m for m in messages(run(invoice()), "error")))

	def test_b2c_skips_buyer_address(self):
		S.get("Customer", "Acme").update(custom_b2c=1, customer_primary_address=None, custom_buyer_id="")
		self.assertEqual(run(invoice())["status"], checks.STATUS_PASSED)

	def test_invoice_address_differs_from_primary(self):
		address("ACME-BRANCH")
		warnings = messages(run(invoice(customer_address="ACME-BRANCH")), "warning")
		self.assertTrue(any("Primary Address" in m for m in warnings))

	def test_discount_on_grand_total(self):
		doc = invoice(discount_amount=10, apply_discount_on="Grand Total",
		              custom_zatca_discount_reason_code="95", custom_zatca_discount_reason="Discount")
		self.assertTrue(any("Net Total" in m for m in messages(run(doc), "error")))

	def test_discount_needs_reason_code(self):
		doc = invoice(discount_amount=10, apply_discount_on="Net Total")
		self.assertTrue(any("reason code" in m for m in messages(run(doc), "error")))

	def test_mixed_vat_categories_with_document_discount(self):
		S.add(E("Item Tax Template", fields=("custom_zatca_tax_category",), name="VAT15",
		        custom_zatca_tax_category="Standard", taxes=[E("Item Tax Template Detail", tax_rate=15)]))
		S.add(E("Item Tax Template", fields=("custom_zatca_tax_category",), name="ZERO",
		        custom_zatca_tax_category="Zero Rated", taxes=[E("Item Tax Template Detail", tax_rate=0)]))
		doc = invoice(
			items=[item(1, item_tax_template="VAT15"), item(2, item_tax_template="ZERO")],
			discount_amount=10, apply_discount_on="Net Total",
			custom_zatca_discount_reason_code="95", custom_zatca_discount_reason="Discount",
		)
		self.assertTrue(any("mixes VAT categories" in m for m in messages(run(doc), "error")))

	def test_template_category_must_match_rate(self):
		S.add(E("Item Tax Template", fields=("custom_zatca_tax_category",), name="BAD",
		        custom_zatca_tax_category="Zero Rated", taxes=[E("Item Tax Template Detail", tax_rate=15)]))
		doc = invoice(items=[item(1, item_tax_template="BAD")])
		self.assertTrue(any("'Standard'" in m for m in messages(run(doc), "error")))

	def test_partial_templates(self):
		S.add(E("Item Tax Template", fields=("custom_zatca_tax_category",), name="VAT15",
		        custom_zatca_tax_category="Standard", taxes=[E("Item Tax Template Detail", tax_rate=15)]))
		doc = invoice(items=[item(1, item_tax_template="VAT15"), item(2)])
		self.assertTrue(any("every row or none" in m for m in messages(run(doc), "error")))

	def test_extra_tax_rows_warn(self):
		doc = invoice(taxes=[E("Sales Taxes and Charges", rate=15, tax_amount=15),
		                     E("Sales Taxes and Charges", rate=0, tax_amount=50)])
		self.assertTrue(any("first row as VAT" in m for m in messages(run(doc), "warning")))

	def test_credit_note_needs_original(self):
		doc = invoice(is_return=1, return_against=None,
		              items=[item(1, qty=-1, amount=-100)], grand_total=-115)
		self.assertTrue(any("Return Against" in m for m in messages(run(doc), "error")))

	def test_nominal_invoice_rules(self):
		doc = invoice(custom_zatca_nominal_invoice=1, additional_discount_percentage=50,
		              custom_submit_line_item_discount_to_zatca=1)
		errors = messages(run(doc), "error")
		self.assertTrue(any("100%" in m for m in errors))
		self.assertTrue(any("nominal invoice cannot report line" in m for m in errors))

	def test_company_not_enabled_stops(self):
		S.get("Company", "Neo")["custom_zatca_invoice_enabled"] = 0
		S.get("Company", "Neo")["tax_id"] = ""
		result = run(invoice())
		self.assertEqual(result["errors"], 0)
		self.assertTrue(any("not enabled" in m for m in messages(result, "info")))

	def test_future_date_warns(self):
		future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
		self.assertTrue(any("future" in m for m in messages(run(invoice(posting_date=future)), "warning")))

	def test_neocharges_rows_in_taxes(self):
		doc = invoice(taxes=[E("Sales Taxes and Charges", rate=15, tax_amount=15),
		                     E("Sales Taxes and Charges", rate=0, tax_amount=20, custom_neocharge_row="X")])
		self.assertTrue(any("NeoCharges charge row" in m for m in messages(run(doc), "error")))

	def test_customer_charge_as_revenue_warns(self):
		S.add(E("Account", name="Delivery Income - N", root_type="Income"))
		doc = invoice(custom_neocharges=[E("NeoCharge Line", idx=1, apply_to="None", party_type="Customer",
		                                    credit_account="Delivery Income - N", charge_description="Delivery")])
		self.assertTrue(any("carries no VAT" in m for m in messages(run(doc), "warning")))

	def test_no_vendor_runs_universal_rules_only(self):
		reset(erpgulf=False)
		S.get("Address", "NEO-ADDR")["custom_building_number"] = "x"
		result = run(invoice())
		self.assertFalse(any("Building Number" in m for m in messages(result)))
		self.assertTrue(any("No ZATCA app" in m for m in messages(result, "info")))


if __name__ == "__main__":
	unittest.main()

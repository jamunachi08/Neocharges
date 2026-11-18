import frappe
from frappe.utils import flt

from neocharges.constants import CHARGE_ROW_DOCTYPE, CHARGE_TOTAL_FIELD, SUPPORTED_DOCTYPES


def execute():
	"""v2.3: give every existing charge line a currency, rate and company amount.

	Lines are recorded with the meaning they actually had when posted:

	* Journal Entry and inline lines posted the amount in company currency.
	* Submitted lines that went into the taxes table (Add To) on a document in
	  a foreign currency were read by ERPNext in the document's currency.
	  They are recorded that way, so history matches the ledger.

	Draft lines are recorded in company currency, which is what the amount
	field was documented to mean. On a foreign-currency draft with Add To
	lines, the next save converts them correctly into the taxes table.
	"""
	if not frappe.db.exists("DocType", CHARGE_ROW_DOCTYPE):
		return

	frappe.reload_doc("charges", "doctype", "neocharge_line")
	frappe.reload_doc("charges", "doctype", "neocharge_template_line")

	frappe.db.sql(
		"""update `tabNeoCharge Template Line`
		set currency_basis = 'Company Currency'
		where ifnull(currency_basis, '') = ''"""
	)

	from neocharges.charges.custom_fields import sync_custom_fields

	sync_custom_fields()

	for parenttype in SUPPORTED_DOCTYPES:
		if not frappe.db.table_exists(parenttype) or not frappe.db.has_column(parenttype, "currency"):
			continue
		_backfill(parenttype)


def _backfill(parenttype):
	rows = frappe.db.sql(
		f"""
		select line.name, line.parent, line.amount, line.apply_to,
			parent.docstatus, parent.currency, parent.conversion_rate,
			company.default_currency
		from `tabNeoCharge Line` line
		join `tab{parenttype}` parent on parent.name = line.parent
		join `tabCompany` company on company.name = parent.company
		where line.parenttype = %s and ifnull(line.charge_currency, '') = ''
		""",
		parenttype,
		as_dict=True,
	)

	totals = {}
	for row in rows:
		native = (row.apply_to or "None") != "None"
		foreign = row.currency and row.currency != row.default_currency
		if native and foreign and row.docstatus != 0:
			currency, rate = row.currency, flt(row.conversion_rate) or 1.0
		else:
			currency, rate = row.default_currency, 1.0

		base = flt(flt(row.amount) * rate, 2)
		frappe.db.set_value(
			CHARGE_ROW_DOCTYPE,
			row.name,
			{"charge_currency": currency, "exchange_rate": rate, "base_amount": base},
			update_modified=False,
		)
		totals[row.parent] = totals.get(row.parent, 0.0) + base

	if totals and frappe.db.has_column(parenttype, CHARGE_TOTAL_FIELD):
		for parent, total in totals.items():
			frappe.db.set_value(parenttype, parent, CHARGE_TOTAL_FIELD, flt(total, 2), update_modified=False)

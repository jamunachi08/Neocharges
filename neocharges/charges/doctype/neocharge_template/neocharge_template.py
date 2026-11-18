import frappe
from frappe import _
from frappe.utils import flt
from frappe.model.document import Document

from neocharges.charges.posting import get_account_details, requires_party
from neocharges.constants import PARTY_TYPE_BY_ACCOUNT_TYPE


class NeoChargeTemplate(Document):
	def validate(self):
		for row in self.lines:
			self._validate_line(row)

	def _validate_line(self, row):
		label = _("Line {0}").format(row.idx)

		if (row.get("currency_basis") or "") == "Specific Currency" and not row.get("currency"):
			frappe.throw(_("{0}: choose the Specific Currency.").format(label))
		if row.get("charge_basis") == "Percentage" and (row.get("currency_basis") or "") == "Specific Currency":
			frappe.throw(
				_(
					"{0}: a percentage line is computed on the document's totals, so it is always in "
					"document currency. Set Currency to Document Currency."
				).format(label)
			)
		if (row.get("currency_basis") or "") != "Specific Currency":
			row.currency = None

		treatment = row.get("vat_treatment") or "No VAT"
		if treatment != "No VAT" and not (0 <= flt(row.get("vat_rate")) <= 100):
			frappe.throw(_("{0}: VAT rate must be between 0 and 100").format(label))
		if treatment == "No VAT":
			row.vat_rate = 0
			row.vat_direction = None
			row.vat_account = None
		else:
			if not flt(row.get("vat_rate")):
				row.vat_rate = flt(frappe.db.get_single_value("NeoCharges Settings", "default_vat_rate") or 15)
			if row.get("vat_account"):
				self._validate_account(label, row.vat_account)

		if (row.get("apply_to") or "None") == "Invoice Line":
			# Added to the invoice as an item line; ERPNext applies the invoice's VAT.
			row.vat_account = None
			row.vat_direction = None
			if row.get("account_head"):
				self._validate_account(label, row.account_head)
			if row.get("charge_basis") == "Percentage" and (row.get("percentage_on") or "Net Total") != "Net Total":
				frappe.throw(_("{0}: an Invoice Line charge can only be a percentage of Net Total.").format(label))
			return

		if (row.get("apply_to") or "None") != "None":
			# Add To lines post through the document's taxes table on account_head.
			if not row.get("account_head"):
				frappe.throw(_("{0}: Account Head is required for Add To lines.").format(label))
			self._validate_account(label, row.account_head)
			return

		# Blank debit or credit account: the document's own account is used
		# when the template is applied (receivable/revenue for selling,
		# expense/payable for buying).
		if row.debit_account and row.debit_account == row.credit_account:
			frappe.throw(_("{0}: debit and credit account must be different").format(label))

		debit = self._validate_account(label, row.debit_account) if row.debit_account else None
		credit = self._validate_account(label, row.credit_account) if row.credit_account else None

		if requires_party(debit) and requires_party(credit):
			frappe.throw(
				_("{0}: both accounts are party accounts. Split this into two lines.").format(label)
			)

		party_details = debit if requires_party(debit) else (credit if requires_party(credit) else None)
		if party_details and row.party_type:
			expected = PARTY_TYPE_BY_ACCOUNT_TYPE.get(party_details.account_type)
			if expected and row.party_type != expected:
				frappe.throw(
					_("{0}: expected Party Type {1} for this account type.").format(label, _(expected))
				)

	def _validate_account(self, label, account):
		details = get_account_details(account)
		if not details:
			frappe.throw(_("{0}: account {1} does not exist").format(label, account))
		if details.is_group:
			frappe.throw(_("{0}: {1} is a group account").format(label, account))
		if details.company != self.company:
			frappe.throw(
				_("{0}: account {1} belongs to {2}, not {3}").format(
					label, account, details.company, self.company
				)
			)
		return details

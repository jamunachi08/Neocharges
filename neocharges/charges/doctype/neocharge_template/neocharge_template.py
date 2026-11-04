import frappe
from frappe import _
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

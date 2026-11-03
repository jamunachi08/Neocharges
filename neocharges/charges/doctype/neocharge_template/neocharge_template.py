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

		if row.debit_account == row.credit_account:
			frappe.throw(_("{0}: debit and credit account must be different").format(label))

		debit = get_account_details(row.debit_account)
		credit = get_account_details(row.credit_account)

		for account, details in ((row.debit_account, debit), (row.credit_account, credit)):
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

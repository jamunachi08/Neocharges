import frappe
from frappe import _
from frappe.model.document import Document


class NeoDiscountReason(Document):
	def validate(self):
		seen = set()
		for row in self.accounts:
			if row.company in seen:
				frappe.throw(_("Row {0}: {1} is listed more than once").format(row.idx, row.company))
			seen.add(row.company)

			account = frappe.get_cached_value(
				"Account", row.discount_account, ["company", "is_group", "disabled"], as_dict=True
			)
			if not account:
				frappe.throw(_("Row {0}: account {1} does not exist").format(row.idx, row.discount_account))
			if account.company != row.company:
				frappe.throw(
					_("Row {0}: account {1} belongs to {2}, not {3}").format(
						row.idx, row.discount_account, account.company, row.company
					)
				)
			if account.is_group or account.disabled:
				frappe.throw(
					_("Row {0}: account {1} is a group or disabled account").format(row.idx, row.discount_account)
				)

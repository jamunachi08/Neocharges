"""Whitelisted endpoints used by the form scripts."""

import frappe
from frappe import _

from neocharges.charges.posting import get_doctype_config, warn_if_pre_accounting
from neocharges.constants import TEMPLATE_DOCTYPE


@frappe.whitelist()
def get_form_config(doctype):
	"""Return the runtime config the form script needs, or None if disabled."""
	config = get_doctype_config(doctype)
	if not config:
		return None

	return {
		"enabled": 1,
		"auto_apply": int(config.auto_apply or 0),
		"default_template": config.default_template,
		"submit_journal_entry": int(config.submit_journal_entry or 0),
		"is_pre_accounting": int(warn_if_pre_accounting(doctype)),
	}


@frappe.whitelist()
def get_template_lines(template, company):
	"""Expand a charge template into rows for the transaction table."""
	frappe.has_permission(TEMPLATE_DOCTYPE, "read", throw=True)

	doc = frappe.get_cached_doc(TEMPLATE_DOCTYPE, template)
	if doc.disabled:
		frappe.throw(_("Charge template {0} is disabled").format(template))
	if company and doc.company != company:
		frappe.throw(
			_("Charge template {0} belongs to {1}, not {2}").format(template, doc.company, company)
		)

	return [
		{
			"charge_description": row.charge_description,
			"charge_description_ar": row.charge_description_ar,
			"charge_basis": row.charge_basis or "Amount",
			"percentage": row.percentage,
			"percentage_on": row.percentage_on,
			"amount": row.default_amount,
			"debit_account": row.debit_account,
			"credit_account": row.credit_account,
			"party_type": row.party_type,
			"party": row.party,
			"cost_center": row.cost_center,
			"project": row.project,
		}
		for row in doc.lines
	]


@frappe.whitelist()
def get_account_party_requirement(account):
	"""Tell the form whether an account needs a party, and of which type."""
	from neocharges.charges.posting import get_account_details, requires_party
	from neocharges.constants import PARTY_TYPE_BY_ACCOUNT_TYPE

	details = get_account_details(account)
	if not details:
		return {"requires_party": 0}

	return {
		"requires_party": int(requires_party(details)),
		"account_type": details.account_type,
		"party_type": PARTY_TYPE_BY_ACCOUNT_TYPE.get(details.account_type),
	}

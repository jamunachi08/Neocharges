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
			"apply_to": row.apply_to or "None",
			"distribute_based_on": row.distribute_based_on or "Amount",
			"account_head": row.account_head,
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


# ---------------------------------------------------------------------------
# Item discounts
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_discount_config(doctype):
	"""Runtime config for the item discount form script, or None if off."""
	from frappe.utils import cint

	from neocharges.constants import ROUNDING_DOCUMENT
	from neocharges.constants import MODE_KEEP
	from neocharges.discounts.engine import discount_mode, get_settings, is_discount_enabled, keep_rate_supported
	from neocharges.discounts.zatca import field_map, zatca_app_installed

	if not is_discount_enabled(doctype):
		return None

	settings = get_settings()
	zatca_doctype = doctype in ("Sales Invoice", "POS Invoice")
	keep = discount_mode(settings) == MODE_KEEP and keep_rate_supported(doctype)
	return {
		"enabled": 1,
		"mode": "keep" if keep else "reduce",
		"zatca_safe": int((settings.get("discount_rounding") or "") != ROUNDING_DOCUMENT),
		"allow_full": cint(settings.get("allow_full_discount")),
		"require_reason": cint(settings.get("require_discount_reason")),
		"default_reason": settings.get("default_discount_reason"),
		"zatca_active": int(
			zatca_doctype and cint(settings.get("zatca_integration")) and zatca_app_installed()
		),
		"zatca_flag_field": field_map(settings).flag,
	}


@frappe.whitelist()
def zatca_discount_preview(doctype, name):
	"""Per-line ZATCA price block, as the e-invoice will print it."""
	from neocharges.discounts.zatca import get_line_allowances

	if doctype not in ("Sales Invoice", "POS Invoice"):
		frappe.throw(_("ZATCA preview is available for Sales Invoice and POS Invoice only"))

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	return get_line_allowances(doc)

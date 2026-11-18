"""ZATCA vendor profiles.

A profile tells NeoCharges which ZATCA e-invoicing app is on the site, which
custom fields that app reads, and which of its own submission rules the
pre-submit check should reproduce. The check only reads fields that exist on
the site, so a profile pointing at a renamed field degrades to a skipped rule,
never to an error.

Add a profile here to support another vendor; nothing else needs to change.
"""

from dataclasses import dataclass, field

import frappe

from neocharges.constants import (
	SETTINGS_DOCTYPE,
	ZATCA_FLAG_FIELD,
	ZATCA_NOMINAL_FIELD,
	ZATCA_REASON_CODE_FIELD,
	ZATCA_REASON_FIELD,
)

VENDOR_AUTO = "Auto-detect"
VENDOR_ERPGULF = "ERPGulf"
VENDOR_OTHER = "Other"
VENDOR_NONE = "None"
VENDOR_CHOICES = (VENDOR_AUTO, VENDOR_ERPGULF, VENDOR_OTHER, VENDOR_NONE)

# Vendor-specific rules the check can reproduce. Universal ZATCA rules run for
# every vendor and are not listed here.
RULE_COMPANY_SWITCH = "company_switch"  # company-level enable / phase / skip flags
RULE_SELLER_ADDRESS = "seller_address"  # which address the app reports, and its format
RULE_COST_CENTER_BRANCH = "cost_center_branch"
RULE_BUYER = "buyer"  # B2B/B2C split, buyer id, primary address
RULE_TAX_TEMPLATES = "tax_templates"  # all-or-none, category vs rate, exemption reason
RULE_FIRST_TAX_ROW = "first_tax_row"  # VAT read from the first taxes row only
RULE_NOMINAL = "nominal"
RULE_DOC_DISCOUNT_CATEGORY = "doc_discount_category"


@dataclass(frozen=True)
class VendorProfile:
	key: str
	label: str
	apps: tuple = ()
	fields: dict = field(default_factory=dict)
	rules: frozenset = frozenset()

	def f(self, name):
		return self.fields.get(name)


ERPGULF = VendorProfile(
	key=VENDOR_ERPGULF,
	label="ERPGulf ZATCA",
	apps=("zatca_erpgulf",),
	fields={
		# invoice
		"line_flag": ZATCA_FLAG_FIELD,
		"reason_code": ZATCA_REASON_CODE_FIELD,
		"reason": ZATCA_REASON_FIELD,
		"nominal": ZATCA_NOMINAL_FIELD,
		"skip_flag": "custom_zatca_pmm",
		"status": "custom_zatca_status",
		"return_against_alt": "custom_return_against_for_zatca",
		"invoice_exemption_code": "custom_exemption_reason_code",
		"invoice_exemption_reason": "custom_tax_exemption_reason",
		# company
		"company_enabled": "custom_zatca_invoice_enabled",
		"company_phase": "custom_phase_1_or_2",
		"company_cost_center": "custom_costcenter",
		"company_line_flag": ZATCA_FLAG_FIELD,
		# cost center
		"cc_branch_address": "custom_zatca_branch_address",
		"cc_registration_type": "custom_zatca__registration_type",
		"cc_registration_number": "custom_zatca__registration_number",
		# customer and address
		"customer_b2c": "custom_b2c",
		"buyer_id": "custom_buyer_id",
		"buyer_id_type": "custom_buyer_id_type",
		"building_number": "custom_building_number",
		# item tax template
		"template_category": "custom_zatca_tax_category",
		"template_exemption_code": "custom_exemption_reason_code",
		"template_exemption_reason": "custom_tax_exemption_reason",
	},
	rules=frozenset(
		{
			RULE_COMPANY_SWITCH,
			RULE_SELLER_ADDRESS,
			RULE_COST_CENTER_BRANCH,
			RULE_BUYER,
			RULE_TAX_TEMPLATES,
			RULE_FIRST_TAX_ROW,
			RULE_NOMINAL,
			RULE_DOC_DISCOUNT_CATEGORY,
		}
	),
)


def _other_profile(settings):
	return VendorProfile(
		key=VENDOR_OTHER,
		label="Other ZATCA app",
		fields={
			"line_flag": (settings.get("zatca_flag_field") or ZATCA_FLAG_FIELD).strip(),
			"reason_code": (settings.get("zatca_reason_code_field") or ZATCA_REASON_CODE_FIELD).strip(),
			"reason": (settings.get("zatca_reason_field") or ZATCA_REASON_FIELD).strip(),
			"nominal": ZATCA_NOMINAL_FIELD,
		},
		rules=frozenset({RULE_NOMINAL, RULE_DOC_DISCOUNT_CATEGORY}),
	)


NONE_PROFILE = VendorProfile(key=VENDOR_NONE, label="No ZATCA app")


def _erpgulf_compatible_installed():
	"""ERPGulf itself, or a fork that kept its field convention."""
	apps = set(frappe.get_installed_apps())
	if apps & set(ERPGULF.apps):
		return True
	if not any("zatca" in app.lower() for app in apps):
		return False
	return frappe.get_meta("Sales Invoice").has_field(ZATCA_FLAG_FIELD) and frappe.get_meta(
		"Customer"
	).has_field("custom_b2c")


def get_profile(settings=None):
	if settings is None:
		settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	choice = settings.get("zatca_vendor") or VENDOR_AUTO

	if choice == VENDOR_ERPGULF:
		return ERPGULF
	if choice == VENDOR_OTHER:
		return _other_profile(settings)
	if choice == VENDOR_NONE:
		return NONE_PROFILE

	if _erpgulf_compatible_installed():
		return ERPGULF
	if any("zatca" in app.lower() for app in frappe.get_installed_apps()):
		return _other_profile(settings)
	return NONE_PROFILE


def vendor_installed(profile):
	if profile.key == VENDOR_NONE:
		return False
	apps = set(frappe.get_installed_apps())
	if profile.apps and apps & set(profile.apps):
		return True
	if profile.key == VENDOR_ERPGULF:
		return _erpgulf_compatible_installed()
	return any("zatca" in app.lower() for app in apps)

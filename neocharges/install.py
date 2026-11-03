"""Idempotent install and migrate seeders."""

import frappe

from neocharges.charges.custom_fields import JOURNAL_ENTRY_FIELDS, sync_custom_fields
from neocharges.constants import SETTINGS_DOCTYPE
from neocharges.discounts.custom_fields import sync_discount_fields
from neocharges.discounts.reasons import reason_name, seed_reasons

# Shipped defaults. Only the four GL-posting documents are enabled out of the
# box; the order and quotation doctypes are listed but off, so the accrual
# behaviour is an explicit choice rather than a surprise.
DEFAULT_CONFIGS = [
	{"document_type": "Purchase Invoice", "enabled": 1, "submit_journal_entry": 1},
	{"document_type": "Purchase Receipt", "enabled": 1, "submit_journal_entry": 1},
	{"document_type": "Sales Invoice", "enabled": 1, "submit_journal_entry": 1},
	{"document_type": "Delivery Note", "enabled": 1, "submit_journal_entry": 1},
	{"document_type": "Purchase Order", "enabled": 0, "submit_journal_entry": 1},
	{"document_type": "Sales Order", "enabled": 0, "submit_journal_entry": 1},
	{"document_type": "Supplier Quotation", "enabled": 0, "submit_journal_entry": 1},
	{"document_type": "Quotation", "enabled": 0, "submit_journal_entry": 1},
]

# Item discounts: the selling chain is on, so a discount set on a quotation
# carries through order, delivery and invoice. POS Invoice and the buying
# chain are listed but off.
DEFAULT_DISCOUNT_CONFIGS = [
	{"document_type": "Quotation", "enabled": 1},
	{"document_type": "Sales Order", "enabled": 1},
	{"document_type": "Delivery Note", "enabled": 1},
	{"document_type": "Sales Invoice", "enabled": 1},
	{"document_type": "POS Invoice", "enabled": 0},
	{"document_type": "Supplier Quotation", "enabled": 0},
	{"document_type": "Purchase Order", "enabled": 0},
	{"document_type": "Purchase Receipt", "enabled": 0},
	{"document_type": "Purchase Invoice", "enabled": 0},
]

DEFAULT_REASON = reason_name("95", "Discount")


def after_install():
	seed_settings()
	setup_item_discounts(first_run=True)
	sync_custom_fields()
	frappe.db.commit()


def after_migrate():
	seed_settings()
	setup_item_discounts(first_run=False)
	sync_custom_fields()
	frappe.db.commit()


def seed_settings():
	"""Create the Settings single with defaults, without clobbering user edits."""
	settings = frappe.get_single(SETTINGS_DOCTYPE)

	existing = {row.document_type for row in settings.doctype_configs}
	added = False

	for config in DEFAULT_CONFIGS:
		if config["document_type"] in existing:
			continue
		settings.append("doctype_configs", config)
		added = True

	if settings.get("enabled") is None:
		settings.enabled = 1
		added = True

	if added or not settings.get("__islocal"):
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)


def setup_item_discounts(first_run=False):
	"""Seed reasons and per-doctype rows; never override a user's choice.

	`first_run` (fresh install, or the v2.0 upgrade patch) switches the
	feature on and sets the default reason. Later migrates only add rows
	that are missing.
	"""
	seed_reasons()

	settings = frappe.get_single(SETTINGS_DOCTYPE)
	existing = {row.document_type for row in settings.get("discount_doctypes") or []}
	changed = False

	for config in DEFAULT_DISCOUNT_CONFIGS:
		if config["document_type"] in existing:
			continue
		settings.append("discount_doctypes", config)
		changed = True

	if first_run:
		if not settings.get("enable_item_discounts"):
			settings.enable_item_discounts = 1
			changed = True
		if not settings.get("default_discount_reason") and frappe.db.exists(
			"NeoDiscount Reason", DEFAULT_REASON
		):
			settings.default_discount_reason = DEFAULT_REASON
			changed = True
		for fieldname, value in (
			("discount_rounding", "ZATCA-safe (2 decimals)"),
			("zatca_mixed_reasons", "Use the reason with the largest discount"),
			("zatca_reason_language", "English"),
			("authority_scope", "Selling documents"),
			("zatca_flag_field", "custom_submit_line_item_discount_to_zatca"),
			("zatca_reason_code_field", "custom_zatca_discount_reason_code"),
			("zatca_reason_field", "custom_zatca_discount_reason"),
		):
			if not settings.get(fieldname):
				settings.set(fieldname, value)
				changed = True
		for fieldname in ("zatca_integration", "require_discount_reason", "block_zatca_tax_charges"):
			if not settings.get(fieldname):
				settings.set(fieldname, 1)
				changed = True

	if changed:
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)
	else:
		sync_discount_fields()


def before_uninstall():
	"""Leave posted data alone; only drop fields with no history."""
	from neocharges.charges.custom_fields import remove_custom_fields
	from neocharges.constants import DISCOUNT_DOCTYPES, SUPPORTED_DOCTYPES
	from neocharges.discounts.custom_fields import remove_discount_fields

	remove_custom_fields(list(SUPPORTED_DOCTYPES))
	remove_discount_fields(list(DISCOUNT_DOCTYPES))

	for field in JOURNAL_ENTRY_FIELDS:
		name = "Journal Entry-{}".format(field["fieldname"])
		has_data = frappe.db.exists(
			"Journal Entry", {"custom_neocharge_source_document": ("is", "set")}
		)
		if has_data:
			break
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True)

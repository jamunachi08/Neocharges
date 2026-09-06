"""Idempotent install and migrate seeders."""

import frappe

from neocharges.charges.custom_fields import JOURNAL_ENTRY_FIELDS, sync_custom_fields
from neocharges.constants import SETTINGS_DOCTYPE

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


def after_install():
	seed_settings()
	sync_custom_fields()
	frappe.db.commit()


def after_migrate():
	seed_settings()
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


def before_uninstall():
	"""Leave posted data alone; only drop fields with no charge history."""
	from neocharges.charges.custom_fields import remove_custom_fields
	from neocharges.constants import SUPPORTED_DOCTYPES

	remove_custom_fields(list(SUPPORTED_DOCTYPES))

	for field in JOURNAL_ENTRY_FIELDS:
		name = "Journal Entry-{}".format(field["fieldname"])
		has_data = frappe.db.exists(
			"Journal Entry", {"custom_neocharge_source_document": ("is", "set")}
		)
		if has_data:
			break
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True)

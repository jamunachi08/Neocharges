"""Validation and Journal Entry posting for NeoCharges.

Charge lines live in a custom child table outside `taxes`, so they can never
influence the source document's net total, tax total or grand total. Each line
produces one debit row and one credit row in a single standalone Journal Entry
that references the source document.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from neocharges.constants import (
	APPLY_FIELD,
	CHARGE_TABLE_FIELD,
	GL_POSTING_DOCTYPES,
	JE_REFERENCE_FIELD,
	PARTY_ACCOUNT_TYPES,
	PARTY_TYPE_BY_ACCOUNT_TYPE,
	SETTINGS_DOCTYPE,
	SUPPORTED_DOCTYPES,
)

ACCOUNT_FIELDS = (
	"company",
	"is_group",
	"account_type",
	"account_currency",
	"disabled",
	"freeze_account",
)


# ---------------------------------------------------------------------------
# Settings access
# ---------------------------------------------------------------------------
def get_settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def get_doctype_config(doctype):
	"""Return the active config row for a doctype, or None if not enabled."""
	if doctype not in SUPPORTED_DOCTYPES:
		return None
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return None

	settings = get_settings()
	if not settings.enabled:
		return None

	for row in settings.doctype_configs:
		if row.document_type == doctype and row.enabled:
			return row
	return None


def is_enabled(doctype):
	return bool(get_doctype_config(doctype))


# ---------------------------------------------------------------------------
# Account helpers
# ---------------------------------------------------------------------------
def get_account_details(account):
	return frappe.get_cached_value("Account", account, ACCOUNT_FIELDS, as_dict=True)


def requires_party(account_details):
	return bool(account_details) and account_details.account_type in PARTY_ACCOUNT_TYPES


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_charges(doc, method=None):
	rows = doc.get(CHARGE_TABLE_FIELD) or []
	if not rows:
		return

	if not is_enabled(doc.doctype):
		frappe.throw(
			_("Additional charges are not enabled for {0}. Enable it in NeoCharges Settings.").format(
				_(doc.doctype)
			)
		)

	if not doc.get(APPLY_FIELD):
		# Lines exist but the switch is off — refuse silently dropping them.
		frappe.throw(
			_("Charge lines are present but 'Apply Additional Charges' is unchecked. "
			  "Tick the box or clear the Charge Lines table.")
		)

	settings = get_settings()
	for row in rows:
		_validate_row(doc, row, settings)


def _validate_row(doc, row, settings):
	label = _("Charge row {0}").format(row.idx)

	if not row.charge_description:
		frappe.throw(_("{0}: Description is required").format(label))

	if not (row.debit_account and row.credit_account):
		frappe.throw(_("{0}: both a debit account and a credit account are required").format(label))

	if row.debit_account == row.credit_account:
		frappe.throw(_("{0}: debit and credit account must be different").format(label))

	if flt(row.amount) <= 0:
		frappe.throw(_("{0}: amount must be greater than zero").format(label))

	debit = get_account_details(row.debit_account)
	credit = get_account_details(row.credit_account)

	for account_name, details in ((row.debit_account, debit), (row.credit_account, credit)):
		if not details:
			frappe.throw(_("{0}: account {1} does not exist").format(label, account_name))
		if details.is_group:
			frappe.throw(_("{0}: {1} is a group account and cannot be posted to").format(label, account_name))
		if details.disabled:
			frappe.throw(_("{0}: account {1} is disabled").format(label, account_name))
		if details.get("freeze_account") == "Yes":
			frappe.throw(_("{0}: account {1} is frozen").format(label, account_name))
		if details.company != doc.company:
			frappe.throw(
				_("{0}: account {1} belongs to {2}, not {3}").format(
					label, account_name, details.company, doc.company
				)
			)

	_validate_party(doc, row, label, debit, credit)
	_validate_cost_center(doc, row, label, settings)


def _validate_party(doc, row, label, debit, credit):
	"""Party is mandatory on Payable/Receivable accounts and forbidden elsewhere."""
	debit_needs = requires_party(debit)
	credit_needs = requires_party(credit)

	if debit_needs and credit_needs:
		frappe.throw(
			_("{0}: both accounts are party accounts. A single charge line supports one party only — "
			  "split this into two lines.").format(label)
		)

	if not (debit_needs or credit_needs):
		# No party account involved: clear any stray party data so it does not
		# reach the Journal Entry.
		row.party_type = None
		row.party = None
		row.party_side = None
		return

	expected_side = "Debit" if debit_needs else "Credit"
	party_account = row.debit_account if debit_needs else row.credit_account
	account_type = (debit if debit_needs else credit).account_type
	expected_party_type = PARTY_TYPE_BY_ACCOUNT_TYPE.get(account_type)

	row.party_side = expected_side

	if not (row.party_type and row.party):
		frappe.throw(
			_("{0}: {1} is a {2} account, so Party Type and Party are required.").format(
				label, party_account, _(account_type)
			)
		)

	if expected_party_type and row.party_type != expected_party_type:
		frappe.throw(
			_("{0}: account {1} is of type {2} and expects Party Type {3}, not {4}.").format(
				label, party_account, _(account_type), _(expected_party_type), _(row.party_type)
			)
		)

	if not frappe.db.exists(row.party_type, row.party):
		frappe.throw(_("{0}: {1} {2} does not exist").format(label, _(row.party_type), row.party))


def _validate_cost_center(doc, row, label, settings):
	if not settings.require_cost_center:
		return

	cost_center = row.cost_center or settings.default_cost_center or doc.get("cost_center")
	if cost_center:
		return

	# Only income/expense accounts genuinely need a cost center.
	for account in (row.debit_account, row.credit_account):
		root_type = frappe.get_cached_value("Account", account, "root_type")
		if root_type in ("Income", "Expense"):
			frappe.throw(
				_("{0}: Cost Center is required for income/expense account {1}. "
				  "Set it on the row or as a default in NeoCharges Settings.").format(
					label, account
				)
			)


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------
def post_charges(doc, method=None):
	config = get_doctype_config(doc.doctype)
	if not config or not doc.get(APPLY_FIELD):
		return

	rows = [r for r in (doc.get(CHARGE_TABLE_FIELD) or []) if flt(r.amount)]
	if not rows:
		return

	existing = frappe.db.get_value(
		"Journal Entry",
		{
			"custom_neocharge_source_doctype": doc.doctype,
			"custom_neocharge_source_document": doc.name,
			"docstatus": 1,
		},
		"name",
	)
	if existing:
		frappe.msgprint(
			_("Charges for this document were already posted as {0}.").format(
				frappe.utils.get_link_to_form("Journal Entry", existing)
			),
			indicator="orange",
		)
		return

	journal_entry = build_journal_entry(doc, rows, config)
	journal_entry.flags.ignore_permissions = True
	journal_entry.insert()

	if config.submit_journal_entry:
		journal_entry.submit()

	doc.db_set(JE_REFERENCE_FIELD, journal_entry.name, update_modified=False)

	frappe.msgprint(
		_("Additional charges posted as {0}.").format(
			frappe.utils.get_link_to_form("Journal Entry", journal_entry.name)
		),
		indicator="green",
		alert=True,
	)


def build_journal_entry(doc, rows, config=None):
	company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	posting_date = doc.get("posting_date") or doc.get("transaction_date") or nowdate()
	settings = get_settings()

	journal_entry = frappe.new_doc("Journal Entry")
	journal_entry.voucher_type = "Journal Entry"
	journal_entry.company = doc.company
	journal_entry.posting_date = posting_date
	journal_entry.custom_neocharge_source_doctype = doc.doctype
	journal_entry.custom_neocharge_source_document = doc.name
	journal_entry.user_remark = _("Additional charges for {0} {1}").format(_(doc.doctype), doc.name)

	if doc.get("project"):
		journal_entry.project = doc.project

	multi_currency = 0
	for row in rows:
		for side in ("debit", "credit"):
			entry = _build_account_row(
				doc, row, side, company_currency, posting_date, settings
			)
			if entry["account_currency"] != company_currency:
				multi_currency = 1
			journal_entry.append("accounts", entry)

	journal_entry.multi_currency = multi_currency
	return journal_entry


def _build_account_row(doc, row, side, company_currency, posting_date, settings):
	"""Build one Journal Entry account row.

	`row.amount` is always interpreted in company currency. When the target
	account carries a different currency, the account-currency amount is
	derived from it using the exchange rate on the posting date.
	"""
	account = row.debit_account if side == "debit" else row.credit_account
	details = get_account_details(account)
	account_currency = details.account_currency or company_currency

	amount = flt(row.amount)
	exchange_rate = 1.0
	account_amount = amount

	if account_currency != company_currency:
		exchange_rate = _get_exchange_rate(account_currency, company_currency, posting_date)
		account_amount = flt(amount / exchange_rate, 2) if exchange_rate else amount

	entry = {
		"account": account,
		"account_currency": account_currency,
		"exchange_rate": exchange_rate,
		"cost_center": row.cost_center or settings.default_cost_center or doc.get("cost_center"),
		"project": row.project or doc.get("project"),
		"user_remark": row.charge_description,
	}

	if side == "debit":
		entry["debit"] = amount
		entry["debit_in_account_currency"] = account_amount
		entry["credit"] = 0
		entry["credit_in_account_currency"] = 0
	else:
		entry["credit"] = amount
		entry["credit_in_account_currency"] = account_amount
		entry["debit"] = 0
		entry["debit_in_account_currency"] = 0

	if row.party_type and row.party and (row.party_side or "").lower() == side:
		entry["party_type"] = row.party_type
		entry["party"] = row.party

	return entry


def _get_exchange_rate(from_currency, to_currency, posting_date):
	try:
		from erpnext.setup.utils import get_exchange_rate

		return flt(get_exchange_rate(from_currency, to_currency, posting_date)) or 1.0
	except Exception:  # noqa: BLE001 — rate lookup must never block posting
		frappe.log_error(
			title="NeoCharges: exchange rate lookup failed",
			message=frappe.get_traceback(),
		)
		return 1.0


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------
def cancel_charges(doc, method=None):
	name = doc.get(JE_REFERENCE_FIELD)
	if not name or not frappe.db.exists("Journal Entry", name):
		return

	journal_entry = frappe.get_doc("Journal Entry", name)
	if journal_entry.docstatus == 1:
		journal_entry.flags.ignore_permissions = True
		journal_entry.cancel()
	elif journal_entry.docstatus == 0:
		journal_entry.flags.ignore_permissions = True
		journal_entry.delete()

	doc.db_set(JE_REFERENCE_FIELD, None, update_modified=False)


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------
def warn_if_pre_accounting(doctype):
	"""True when charges on this doctype create a standalone accrual."""
	return doctype not in GL_POSTING_DOCTYPES

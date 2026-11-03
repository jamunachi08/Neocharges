"""ZATCA pre-submit compatibility check.

Runs on a draft Sales Invoice or POS Invoice and reports, before anything is
submitted, what would make the ZATCA app refuse the invoice or ZATCA reject
it. Many of those conditions are otherwise only raised by the ZATCA app after
submission, when it builds and sends the XML, by which point the invoice is
already in the books.

Three kinds of rule:
  ZATCA       universal e-invoicing requirements, checked for every vendor
  Vendor      conditions the configured ZATCA app enforces or depends on
              (see vendors.py), checked only when its fields exist
  NeoCharges  this app's own discount and charge figures

Nothing here changes the document. The XML is built and signed by the ZATCA
app; this module only reads.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, nowdate

from neocharges.constants import SETTINGS_DOCTYPE, ZATCA_DOCTYPES
from neocharges.discounts.reasons import REASON_CODES
from neocharges.zatca_check import vendors as V

ERROR = "error"
WARNING = "warning"
INFO = "info"

SOURCE_ZATCA = "ZATCA"
SOURCE_VENDOR = "Vendor"
SOURCE_NEOCHARGES = "NeoCharges"

STATUS_FIELD = "custom_nd_zatca_check"
CHECKED_ON_FIELD = "custom_nd_zatca_checked_on"
STATUS_NOT_CHECKED = "Not Checked"
STATUS_PASSED = "Passed"
STATUS_WARNINGS = "Passed with Warnings"
STATUS_FAILED = "Failed"
STATUSES = (STATUS_NOT_CHECKED, STATUS_PASSED, STATUS_WARNINGS, STATUS_FAILED)

ON_SUBMIT_BLOCK = "Block on errors"
ON_SUBMIT_WARN = "Warn only"
ON_SUBMIT_OFF = "Off"

SAUDI_ARABIA = "Saudi Arabia"
VAT_RATES = (0.0, 5.0, 15.0)
NON_STANDARD_CATEGORIES = (
	"Zero Rated",
	"Exempted",
	"Services outside scope of tax / Not subject to VAT",
)
OUT_OF_SCOPE_CODE = "VATEX-SA-OOS"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
class Report:
	def __init__(self, profile):
		self.profile = profile
		self.findings = []

	def add(self, level, area, message, hint=None, source=SOURCE_ZATCA):
		self.findings.append(
			{"level": level, "area": area, "message": message, "hint": hint, "source": source}
		)

	def error(self, area, message, hint=None, source=SOURCE_ZATCA):
		self.add(ERROR, area, message, hint, source)

	def warning(self, area, message, hint=None, source=SOURCE_ZATCA):
		self.add(WARNING, area, message, hint, source)

	def info(self, area, message, hint=None, source=SOURCE_ZATCA):
		self.add(INFO, area, message, hint, source)

	def count(self, level):
		return sum(1 for f in self.findings if f["level"] == level)

	@property
	def status(self):
		if self.count(ERROR):
			return STATUS_FAILED
		if self.count(WARNING):
			return STATUS_WARNINGS
		return STATUS_PASSED

	def as_dict(self):
		order = {ERROR: 0, WARNING: 1, INFO: 2}
		return {
			"status": self.status,
			"vendor": self.profile.key,
			"vendor_label": self.profile.label,
			"checked_at": str(now_datetime()),
			"errors": self.count(ERROR),
			"warnings": self.count(WARNING),
			"findings": sorted(self.findings, key=lambda f: order[f["level"]]),
		}


class Context:
	def __init__(self, doc, profile, settings):
		self.doc = doc
		self.profile = profile
		self.settings = settings
		self.company = frappe.get_cached_doc("Company", doc.company)
		self.customer = frappe.get_cached_doc("Customer", doc.customer) if doc.get("customer") else None

	def has_rule(self, rule):
		return rule in self.profile.rules

	def field(self, record, logical):
		"""Vendor fieldname on this record, or None if the site doesn't have it."""
		fieldname = self.profile.f(logical)
		if not fieldname or record is None:
			return None
		return fieldname if record.meta.has_field(fieldname) else None

	def value(self, record, logical):
		fieldname = self.field(record, logical)
		return record.get(fieldname) if fieldname else None


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def get_settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def check_enabled(settings=None):
	settings = settings or get_settings()
	return bool(cint(settings.get("zatca_check_enabled")))


def run_checks(doc, settings=None):
	settings = settings or get_settings()
	profile = V.get_profile(settings)
	report = Report(profile)

	if doc.doctype not in ZATCA_DOCTYPES:
		report.info(_("Scope"), _("ZATCA checks apply to Sales Invoice and POS Invoice only."))
		return report.as_dict()

	ctx = Context(doc, profile, settings)

	if profile.key == V.VENDOR_NONE:
		report.info(
			_("Vendor"),
			_("No ZATCA app detected. Only universal ZATCA rules are checked."),
			_("Set NeoCharges Settings → ZATCA Vendor if a ZATCA app is installed."),
		)
	elif not V.vendor_installed(profile):
		report.warning(
			_("Vendor"),
			_("ZATCA Vendor is set to {0}, but that app is not installed on this site.").format(profile.label),
			_("Install the app, or change NeoCharges Settings → ZATCA Vendor."),
			SOURCE_NEOCHARGES,
		)

	if not _check_applicability(ctx, report):
		return report.as_dict()

	_check_seller(ctx, report)
	_check_buyer(ctx, report)
	_check_lines(ctx, report)
	_check_tax(ctx, report)
	_check_discounts(ctx, report)
	_check_returns(ctx, report)
	_check_neocharges(ctx, report)
	return report.as_dict()


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
def _check_applicability(ctx, r):
	doc, company = ctx.doc, ctx.company
	area = _("Applicability")

	if (company.get("country") or "") != SAUDI_ARABIA:
		r.info(area, _("Company {0} is not in Saudi Arabia, so ZATCA does not apply.").format(company.name))
		return False

	if ctx.has_rule(V.RULE_COMPANY_SWITCH):
		enabled_field = ctx.field(company, "company_enabled")
		if enabled_field and not cint(company.get(enabled_field)):
			r.info(
				area,
				_("ZATCA is not enabled on Company {0}. The invoice will not be sent.").format(company.name),
				source=SOURCE_VENDOR,
			)
			return False
		if (ctx.value(company, "company_phase") or "") == "Phase-1":
			r.info(
				area,
				_("Company {0} is on Phase 1: the invoice gets a QR code but is not sent to ZATCA.").format(
					company.name
				),
				source=SOURCE_VENDOR,
			)
		if cint(ctx.value(doc, "skip_flag")):
			r.info(area, _("This invoice is marked to skip ZATCA submission."), source=SOURCE_VENDOR)
			return False
		status = ctx.value(doc, "status") or ""
		if status in ("REPORTED", "CLEARED"):
			r.info(area, _("Already {0} by ZATCA.").format(status.lower()), source=SOURCE_VENDOR)

	posting_date = doc.get("posting_date")
	if posting_date and getdate(posting_date) > getdate(nowdate()):
		r.warning(
			_("Invoice"),
			_("Posting date {0} is in the future. ZATCA does not accept an issue date after today.").format(
				posting_date
			),
		)

	if (doc.get("currency") or "SAR") != "SAR" and flt(doc.get("conversion_rate")) in (0, 1):
		r.warning(
			_("Invoice"),
			_("Currency is {0} with exchange rate {1}. ZATCA needs the VAT total in SAR.").format(
				doc.currency, doc.get("conversion_rate")
			),
			_("Set the correct exchange rate."),
		)
	return True


def _valid_vat_number(value):
	value = (value or "").strip()
	return value.isdigit() and len(value) == 15 and value.startswith("3") and value.endswith("3")


def _check_seller(ctx, r):
	area = _("Seller")
	company = ctx.company
	tax_id = (company.get("tax_id") or "").strip()

	if not tax_id:
		r.error(area, _("Company {0} has no Tax ID (VAT number).").format(company.name))
	elif not _valid_vat_number(tax_id):
		r.error(
			area,
			_("Company Tax ID {0} is not a valid VAT number: 15 digits, starting and ending with 3.").format(
				tax_id
			),
		)

	address = None
	if ctx.has_rule(V.RULE_COST_CENTER_BRANCH) and cint(ctx.value(company, "company_cost_center")):
		address = _branch_address(ctx, r)
	elif ctx.has_rule(V.RULE_SELLER_ADDRESS):
		address = _vendor_company_address(ctx, r)
	else:
		linked = _addresses_linked_to("Company", company.name, company_only=True)
		if not linked:
			r.warning(area, _("Company {0} has no address.").format(company.name))
		else:
			address = frappe.get_doc("Address", linked[0])

	if address:
		_check_address(ctx, r, address, area, _("company address"))


def _branch_address(ctx, r):
	area = _("Seller")
	doc = ctx.doc
	if not doc.get("cost_center"):
		r.error(
			area,
			_("Company {0} reports per branch (cost center), but this invoice has no Cost Center.").format(
				ctx.company.name
			),
			source=SOURCE_VENDOR,
		)
		return None

	cost_center = frappe.get_cached_doc("Cost Center", doc.cost_center)
	for logical, label in (
		("cc_registration_type", _("registration type")),
		("cc_registration_number", _("registration number")),
	):
		fieldname = ctx.field(cost_center, logical)
		if fieldname and not cost_center.get(fieldname):
			r.error(
				area,
				_("Cost Center {0} has no ZATCA {1}.").format(cost_center.name, label),
				source=SOURCE_VENDOR,
			)

	branch = ctx.value(cost_center, "cc_branch_address")
	if not branch:
		r.error(
			area,
			_("Cost Center {0} has no ZATCA branch address.").format(cost_center.name),
			source=SOURCE_VENDOR,
		)
		return None
	return frappe.get_doc("Address", branch)


def _vendor_company_address(ctx, r):
	"""The address the vendor app will report.

	The app takes the first address flagged 'Your Company Address', using
	the default list order, without filtering by company. Reproduce that
	choice exactly, then flag it if it belongs to another company.
	"""
	area = _("Seller")
	rows = frappe.get_all("Address", filters={"is_your_company_address": 1}, pluck="name")
	if not rows:
		r.error(
			area,
			_("No address is marked 'Your Company Address'. The ZATCA app needs one for the seller."),
			source=SOURCE_VENDOR,
		)
		return None

	address = frappe.get_doc("Address", rows[0])
	companies = {
		link.link_name for link in address.get("links") or [] if link.link_doctype == "Company"
	}
	if companies and ctx.company.name not in companies:
		r.warning(
			area,
			_(
				"The ZATCA app will report seller address {0}, which belongs to {1}, not {2}. It picks the "
				"first 'Your Company Address' on the site, regardless of company."
			).format(address.name, ", ".join(sorted(companies)), ctx.company.name),
			_("On multi-company sites, use branch (cost center) addresses for ZATCA."),
			SOURCE_VENDOR,
		)
	return address


def _addresses_linked_to(link_doctype, link_name, company_only=False):
	filters = {"link_doctype": link_doctype, "link_name": link_name, "parenttype": "Address"}
	names = frappe.get_all("Dynamic Link", filters=filters, pluck="parent")
	if company_only and names:
		flagged = frappe.get_all(
			"Address", filters={"name": ("in", names), "is_your_company_address": 1}, pluck="name"
		)
		return flagged or names
	return names


def _check_address(ctx, r, address, area, what):
	source = SOURCE_VENDOR if ctx.profile.key == V.VENDOR_ERPGULF else SOURCE_ZATCA
	if not address.get("address_line1"):
		r.error(area, _("{0} {1}: Address Line 1 is empty.").format(what.capitalize(), address.name), source=source)
	if ctx.profile.key == V.VENDOR_ERPGULF and not address.get("address_line2"):
		r.error(
			area,
			_("{0} {1}: Address Line 2 (district) is empty.").format(what.capitalize(), address.name),
			source=SOURCE_VENDOR,
		)
	if not address.get("city"):
		r.warning(area, _("{0} {1}: City is empty.").format(what.capitalize(), address.name), source=source)

	if (address.get("country") or "") != SAUDI_ARABIA:
		return

	building_field = ctx.field(address, "building_number")
	if building_field:
		building = (address.get(building_field) or "").strip()
		if not (building.isdigit() and len(building) == 4):
			r.error(
				area,
				_("{0} {1}: Building Number must be exactly 4 digits (found '{2}').").format(
					what.capitalize(), address.name, building
				),
				source=SOURCE_VENDOR,
			)

	pincode = (address.get("pincode") or "").strip()
	if not (pincode.isdigit() and len(pincode) == 5):
		r.error(
			area,
			_("{0} {1}: Postal Code must be exactly 5 digits (found '{2}').").format(
				what.capitalize(), address.name, pincode
			),
		)


def _check_buyer(ctx, r):
	area = _("Buyer")
	customer = ctx.customer
	if not customer:
		return

	tax_id = (customer.get("tax_id") or "").strip()
	buyer_id = (ctx.value(customer, "buyer_id") or "").strip()
	buyer_id_type = ctx.value(customer, "buyer_id_type")

	if buyer_id and ctx.field(customer, "buyer_id_type") and not buyer_id_type:
		r.error(
			area,
			_("Customer {0} has a Buyer ID but no Buyer ID Type.").format(customer.name),
			source=SOURCE_VENDOR,
		)

	if not ctx.has_rule(V.RULE_BUYER):
		if tax_id and not _valid_vat_number(tax_id):
			r.warning(
				area,
				_("Customer Tax ID {0} is not a Saudi VAT number (15 digits, starting and ending with 3).").format(
					tax_id
				),
			)
		return

	b2c_field = ctx.field(customer, "customer_b2c")
	if b2c_field and cint(customer.get(b2c_field)):
		r.info(area, _("B2C customer: reported as a simplified invoice."), source=SOURCE_VENDOR)
		return

	# Standard (B2B) invoice
	if ctx.field(customer, "buyer_id") and not buyer_id:
		r.error(
			area,
			_("Customer {0} is B2B but has no Buyer ID (e.g. CR number).").format(customer.name),
			_("Fill Buyer ID and Buyer ID Type on the customer, or mark the customer B2C."),
			SOURCE_VENDOR,
		)

	primary = customer.get("customer_primary_address")
	if not primary:
		r.error(
			area,
			_("Customer {0} has no Primary Address. B2B invoices need the buyer's address.").format(
				customer.name
			),
			source=SOURCE_VENDOR,
		)
		return

	if ctx.doc.get("customer_address") and ctx.doc.customer_address != primary:
		r.warning(
			area,
			_(
				"The invoice uses address {0}, but the ZATCA app reports the customer's Primary Address {1}."
			).format(ctx.doc.customer_address, primary),
			_("Make {0} the customer's primary address if it is the right one.").format(
				ctx.doc.customer_address
			),
			SOURCE_VENDOR,
		)

	address = frappe.get_doc("Address", primary)
	_check_address(ctx, r, address, area, _("customer address"))

	if (address.get("country") or "") == SAUDI_ARABIA:
		if tax_id and not _valid_vat_number(tax_id):
			r.error(
				area,
				_("Customer Tax ID {0} must be 15 digits, starting and ending with 3.").format(tax_id),
			)
		elif not tax_id and not buyer_id:
			r.error(area, _("Saudi B2B customer {0} needs a Tax ID or a Buyer ID.").format(customer.name))


def _check_lines(ctx, r):
	doc = ctx.doc
	area = _("Lines")
	if doc.get("is_return"):
		return
	for item in doc.get("items") or []:
		if flt(item.qty) <= 0:
			r.error(area, _("Row {0}: quantity must be positive on an invoice.").format(item.idx))
		if flt(item.rate) < 0:
			r.error(area, _("Row {0}: rate cannot be negative on an invoice.").format(item.idx))
	if flt(doc.get("grand_total")) < 0:
		r.error(area, _("Grand total is negative. Use a credit note instead."))


def _template_info(name):
	return frappe.get_cached_doc("Item Tax Template", name)


def _check_tax(ctx, r):
	doc = ctx.doc
	area = _("VAT")
	items = doc.get("items") or []
	taxes = doc.get("taxes") or []
	templates = [item.get("item_tax_template") for item in items]
	with_template = [t for t in templates if t]

	if not taxes and not with_template:
		r.error(area, _("No VAT on this invoice: the taxes table is empty and no item has a tax template."))
		return

	inclusive = bool(taxes and cint(taxes[0].get("included_in_print_rate")))

	if ctx.has_rule(V.RULE_TAX_TEMPLATES):
		if inclusive and with_template:
			r.error(
				area,
				_("Item Tax Templates cannot be used when VAT is included in the print rate."),
				source=SOURCE_VENDOR,
			)
		if with_template and len(with_template) != len(templates):
			missing = [str(item.idx) for item in items if not item.get("item_tax_template")]
			r.error(
				area,
				_("Some rows have an Item Tax Template and rows {0} do not. Use one on every row or none.").format(
					", ".join(missing)
				),
				source=SOURCE_VENDOR,
			)
		for name in sorted(set(with_template)):
			_check_template(ctx, r, name)

		if not with_template:
			code = ctx.value(doc, "invoice_exemption_code")
			if code == OUT_OF_SCOPE_CODE and not ctx.value(doc, "invoice_exemption_reason"):
				r.error(
					area,
					_("Exemption code {0} needs a Tax Exemption Reason on the invoice.").format(OUT_OF_SCOPE_CODE),
					source=SOURCE_VENDOR,
				)

	if not with_template and taxes:
		rate = flt(taxes[0].get("rate"))
		if rate not in VAT_RATES:
			r.warning(
				area,
				_("The first tax row's rate is {0}%. Saudi VAT is 15%, 5% or 0%.").format(rate),
			)

	if ctx.has_rule(V.RULE_FIRST_TAX_ROW):
		active_rows = [t for t in taxes if flt(t.get("tax_amount")) or flt(t.get("rate"))]
		if len(active_rows) > 1:
			r.warning(
				area,
				_(
					"The taxes table has {0} rows. The ZATCA app treats the first row as VAT; other rows "
					"are not reported as separate charges and can make the XML totals disagree."
				).format(len(active_rows)),
				_("Keep only the VAT row; add other charges as items."),
				SOURCE_VENDOR,
			)


def _check_template(ctx, r, name):
	area = _("VAT")
	try:
		template = _template_info(name)
	except frappe.DoesNotExistError:
		r.error(area, _("Item Tax Template {0} does not exist.").format(name))
		return

	category_field = ctx.field(template, "template_category")
	category = template.get(category_field) if category_field else None
	for row in template.get("taxes") or []:
		rate = flt(row.get("tax_rate"))
		if category_field and rate == 15 and category != "Standard":
			r.error(
				area,
				_("Item Tax Template {0}: 15% VAT must have ZATCA category 'Standard' (found '{1}').").format(
					name, category or ""
				),
				source=SOURCE_VENDOR,
			)
		if category_field and rate not in (5.0, 15.0) and category not in NON_STANDARD_CATEGORIES:
			r.error(
				area,
				_(
					"Item Tax Template {0}: a {1}% rate needs ZATCA category Zero Rated, Exempted or "
					"Out of Scope (found '{2}')."
				).format(name, rate, category or ""),
				source=SOURCE_VENDOR,
			)

	code_field = ctx.field(template, "template_exemption_code")
	reason_field = ctx.field(template, "template_exemption_reason")
	if code_field and template.get(code_field) == OUT_OF_SCOPE_CODE and reason_field and not template.get(
		reason_field
	):
		r.error(
			area,
			_("Item Tax Template {0}: exemption code {1} needs a Tax Exemption Reason.").format(
				name, OUT_OF_SCOPE_CODE
			),
			source=SOURCE_VENDOR,
		)


def _vat_categories(ctx):
	categories = set()
	for item in ctx.doc.get("items") or []:
		name = item.get("item_tax_template")
		if not name:
			categories.add("")
			continue
		try:
			template = _template_info(name)
		except frappe.DoesNotExistError:
			categories.add(name)
			continue
		category_field = ctx.field(template, "template_category")
		categories.add(template.get(category_field) if category_field else name)
	return categories


def _check_discounts(ctx, r):
	doc = ctx.doc
	area = _("Discounts")
	discount = flt(doc.get("discount_amount"))

	if discount:
		if (doc.get("apply_discount_on") or "") != "Net Total":
			r.error(
				area,
				_("The Additional Discount is applied on {0}. ZATCA needs it on Net Total, before VAT.").format(
					doc.get("apply_discount_on") or _("nothing")
				),
			)
		if discount < 0 and not doc.get("is_return"):
			r.error(area, _("The Additional Discount is negative."))
		if ctx.has_rule(V.RULE_DOC_DISCOUNT_CATEGORY) and len(_vat_categories(ctx)) > 1:
			r.error(
				area,
				_(
					"A document-level discount on an invoice that mixes VAT categories cannot be sent: "
					"the ZATCA app places the discount in one category."
				),
				_("Split the invoice by VAT category, or use the Reduce Rate item discount method."),
				SOURCE_VENDOR,
			)

	if ctx.has_rule(V.RULE_NOMINAL) and cint(ctx.value(doc, "nominal")):
		if flt(doc.get("additional_discount_percentage")) != 100:
			r.error(
				area,
				_("A nominal invoice needs an Additional Discount of exactly 100%."),
				source=SOURCE_VENDOR,
			)
		if cint(ctx.value(doc, "line_flag")):
			r.error(
				area,
				_("A nominal invoice cannot report line item discounts. Untick the line discount option."),
				source=SOURCE_VENDOR,
			)

	line_flag = cint(ctx.value(doc, "line_flag"))
	reported_discount = discount or (
		line_flag and any(flt(item.get("discount_amount")) for item in doc.get("items") or [])
	)
	if reported_discount and ctx.field(doc, "reason_code"):
		code = (ctx.value(doc, "reason_code") or "").strip()
		if not code:
			r.error(
				area,
				_("The invoice carries a discount but no discount reason code."),
				_("Set a Discount Reason on the discounted lines, or the invoice's reason code."),
				SOURCE_VENDOR,
			)
		elif code not in REASON_CODES:
			r.warning(
				area,
				_("Discount reason code {0} is not a standard allowance code.").format(code),
				_("Use one of: {0}.").format(", ".join(REASON_CODES)),
			)
		if ctx.field(doc, "reason") and not ctx.value(doc, "reason"):
			r.warning(area, _("The discount reason text is empty."), source=SOURCE_VENDOR)

	from neocharges.discounts.zatca import get_line_allowances

	allowances = get_line_allowances(doc, ctx.settings)
	for line in allowances["lines"]:
		if line["allowance_amount"] and not line["checks_ok"]:
			r.error(
				area,
				_("Row {0}: {1}.").format(line["idx"], "; ".join(line["checks"])),
				source=SOURCE_NEOCHARGES,
			)
	document = allowances.get("document_allowance")
	if document and not document["checks_ok"]:
		r.error(
			area,
			_("The document discount does not equal the sum of the item discounts."),
			_("Save the invoice again to recalculate."),
			SOURCE_NEOCHARGES,
		)


def _check_returns(ctx, r):
	doc = ctx.doc
	area = _("Returns")
	if doc.get("is_return") and not doc.get("return_against") and not ctx.value(doc, "return_against_alt"):
		r.error(
			area,
			_("A credit note must reference the original invoice in 'Return Against'."),
		)
	if doc.get("is_debit_note") and not doc.get("return_against"):
		r.error(area, _("A debit note must reference the original invoice in 'Return Against'."))


def _check_neocharges(ctx, r):
	from neocharges.constants import TAX_ROW_MARKER

	rows = [t for t in ctx.doc.get("taxes") or [] if t.get(TAX_ROW_MARKER)]
	if rows:
		r.error(
			_("Charges"),
			_(
				"{0} NeoCharges charge row(s) are in the taxes table and would be reported to ZATCA as VAT."
			).format(len(rows)),
			_("Set those charges to Add To: None."),
			SOURCE_NEOCHARGES,
		)


# ---------------------------------------------------------------------------
# Status and hooks
# ---------------------------------------------------------------------------
def reset_status(doc, method=None):
	"""Any save invalidates an earlier check."""
	if doc.meta.has_field(STATUS_FIELD) and doc.docstatus == 0:
		doc.set(STATUS_FIELD, STATUS_NOT_CHECKED)
		doc.set(CHECKED_ON_FIELD, None)


def check_before_submit(doc, method=None):
	if doc.doctype not in ZATCA_DOCTYPES or not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return
	settings = get_settings()
	if not check_enabled(settings):
		return
	mode = settings.get("zatca_check_on_submit") or ON_SUBMIT_BLOCK
	if mode == ON_SUBMIT_OFF:
		return

	if V.get_profile(settings).key == V.VENDOR_NONE:
		# No ZATCA app: nothing will be sent, so report rather than block.
		mode = ON_SUBMIT_WARN

	result = run_checks(doc, settings)
	if doc.meta.has_field(STATUS_FIELD):
		doc.set(STATUS_FIELD, result["status"])
		doc.set(CHECKED_ON_FIELD, now_datetime())

	errors = [f for f in result["findings"] if f["level"] == ERROR]
	warnings = [f for f in result["findings"] if f["level"] == WARNING]

	if errors and mode == ON_SUBMIT_BLOCK:
		frappe.throw(
			_("This invoice would not pass ZATCA ({0}):").format(result["vendor_label"])
			+ _html_list(errors)
			+ _("Fix these and submit again, or run View → ZATCA Check for details."),
			title=_("ZATCA Check Failed"),
		)
	if errors or warnings:
		frappe.msgprint(
			_html_list(errors + warnings),
			title=_("ZATCA Check: {0}").format(_(result["status"])),
			indicator="red" if errors else "orange",
		)


def _html_list(findings):
	esc = frappe.utils.escape_html
	items = "".join(
		"<li><b>{0}</b>: {1}{2}</li>".format(
			esc(f["area"]),
			esc(f["message"]),
			"<br><small>{0}</small>".format(esc(f["hint"])) if f.get("hint") else "",
		)
		for f in findings
	)
	return f"<ul>{items}</ul>"


def save_status(doc, result):
	"""Record a manual check on a draft without touching its modified time."""
	if doc.docstatus != 0 or not doc.meta.has_field(STATUS_FIELD):
		return
	frappe.db.set_value(
		doc.doctype,
		doc.name,
		{STATUS_FIELD: result["status"], CHECKED_ON_FIELD: now_datetime()},
		update_modified=False,
	)

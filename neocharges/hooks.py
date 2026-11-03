app_name = "neocharges"
app_title = "NeoCharges"
app_publisher = "Neotec Integrated Solutions"
app_description = (
	"Additional charges and ZATCA-compatible item-wise discounts for ERPNext sales "
	"and purchase documents."
)
app_email = "support@neotec.sa"
app_license = "Commercial"
required_apps = ["frappe/erpnext"]

# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
app_include_js = [
	"/assets/neocharges/js/charges.js",
	"/assets/neocharges/js/discounts.js",
	"/assets/neocharges/js/zatca_check.js",
]

# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------
after_install = "neocharges.install.after_install"
after_migrate = "neocharges.install.after_migrate"
before_uninstall = "neocharges.install.before_uninstall"

# ---------------------------------------------------------------------------
# Document events
# ---------------------------------------------------------------------------
# Registered on the fixed supported list rather than "*": the wildcard fires on
# every Version, Error Log and Comment write. Whether a registered doctype is
# actually active is decided at runtime by NeoCharges Settings.
#
# Frappe treats every public module-level name in hooks.py as a hook, so the
# doc_events map is built inside a function and the helper names are discarded
# afterwards. Leaving `SUPPORTED_DOCTYPES` at module scope would publish it as
# a bogus hook of that name.
def _build_doc_events():
	from neocharges.constants import DISCOUNT_DOCTYPES, SUPPORTED_DOCTYPES, ZATCA_DOCTYPES

	events = {}

	def add(doctype, event, handler):
		events.setdefault(doctype, {}).setdefault(event, []).append(handler)

	# Item discounts go first. before_validate runs ahead of the controller's
	# own validate, so ERPNext totals the document from the discounted rates;
	# validate then only verifies nothing repriced the lines in between.
	# Registering them before the charge handlers also means a percentage
	# charge on Net Total is computed from the discounted total.
	for doctype in DISCOUNT_DOCTYPES:
		add(doctype, "before_validate", "neocharges.discounts.engine.apply_item_discounts")
		add(doctype, "validate", "neocharges.discounts.engine.verify_item_discounts")
		add(doctype, "before_submit", "neocharges.discounts.engine.check_discount_authority")

	# ZATCA pre-submit check: any save makes an earlier check stale; submit
	# runs it again (blocking or warning per Settings).
	for doctype in ZATCA_DOCTYPES:
		add(doctype, "before_validate", "neocharges.zatca_check.checks.reset_status")
		add(doctype, "before_submit", "neocharges.zatca_check.checks.check_before_submit")

	for doctype in SUPPORTED_DOCTYPES:
		add(doctype, "validate", "neocharges.charges.posting.validate_charges")
		add(doctype, "on_submit", "neocharges.charges.posting.post_charges")
		add(doctype, "on_cancel", "neocharges.charges.posting.cancel_charges")

	# Repost regenerates a voucher's GL from the source document and drops any
	# inline charge rows with it. This puts them back.
	add("Repost Accounting Ledger", "on_submit", "neocharges.charges.posting.repost_inline_charges")
	return events


doc_events = _build_doc_events()

del _build_doc_events

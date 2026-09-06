app_name = "neocharges"
app_title = "NeoCharges"
app_publisher = "Neotec Integrated Solutions"
app_description = (
	"Document-level debit/credit charge lines that post an independent, "
	"self-balancing Journal Entry without affecting the source document's totals."
)
app_email = "support@neotec.sa"
app_license = "Commercial"
required_apps = ["frappe/erpnext"]

# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
app_include_js = "/assets/neocharges/js/charges.js"

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
	from neocharges.constants import SUPPORTED_DOCTYPES

	handlers = {
		"validate": "neocharges.charges.posting.validate_charges",
		"on_submit": "neocharges.charges.posting.post_charges",
		"on_cancel": "neocharges.charges.posting.cancel_charges",
	}
	events = {doctype: dict(handlers) for doctype in SUPPORTED_DOCTYPES}

	# Repost regenerates a voucher's GL from the source document and drops any
	# inline charge rows with it. This puts them back.
	events["Repost Accounting Ledger"] = {
		"on_submit": "neocharges.charges.posting.repost_inline_charges",
	}
	return events


doc_events = _build_doc_events()

del _build_doc_events

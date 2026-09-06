from neocharges.constants import SUPPORTED_DOCTYPES

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
_charge_events = {
	"validate": "neocharges.charges.posting.validate_charges",
	"on_submit": "neocharges.charges.posting.post_charges",
	"on_cancel": "neocharges.charges.posting.cancel_charges",
}

doc_events = {doctype: dict(_charge_events) for doctype in SUPPORTED_DOCTYPES}

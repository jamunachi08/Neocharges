"""Shared constants for NeoCharges."""

# Document types this app is allowed to attach charges to.
# The Settings page decides which of these are actually live on a given site.
SALES_DOCTYPES = (
	"Quotation",
	"Sales Order",
	"Delivery Note",
	"Sales Invoice",
)

PURCHASE_DOCTYPES = (
	"Supplier Quotation",
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
)

SUPPORTED_DOCTYPES = SALES_DOCTYPES + PURCHASE_DOCTYPES

# Document types that post their own GL entries. The rest are pre-accounting
# documents; charges raised there create a standalone accrual, which is valid
# but must not be repeated downstream. See README "Duplicate posting".
GL_POSTING_DOCTYPES = (
	"Delivery Note",
	"Sales Invoice",
	"Purchase Receipt",
	"Purchase Invoice",
)

# Account types that make party mandatory on a Journal Entry row.
PARTY_ACCOUNT_TYPES = ("Payable", "Receivable")

PARTY_TYPE_BY_ACCOUNT_TYPE = {
	"Payable": "Supplier",
	"Receivable": "Customer",
}

CHARGE_TABLE_FIELD = "custom_neocharges"
APPLY_FIELD = "custom_apply_neocharges"
TEMPLATE_FIELD = "custom_neocharge_template"
JE_REFERENCE_FIELD = "custom_neocharges_je"

SETTINGS_DOCTYPE = "NeoCharges Settings"
CHARGE_ROW_DOCTYPE = "NeoCharge Line"
TEMPLATE_DOCTYPE = "NeoCharge Template"

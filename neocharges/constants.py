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

# Posting modes
POSTING_MODE_JOURNAL = "Journal Entry"
POSTING_MODE_INLINE = "Inline with Document GL"
POSTING_MODES = (POSTING_MODE_JOURNAL, POSTING_MODE_INLINE)

# Percentage bases. Values are company-currency fields on the source document.
BASE_FIELD_BY_BASIS = {
	"Net Total": "base_net_total",
	"Grand Total": "base_grand_total",
	"Total Taxes and Charges": "base_total_taxes_and_charges",
}

APPLY_TO_NONE = "None"
APPLY_TO_TOTAL = "Total"
APPLY_TO_VALUATION = "Valuation"
APPLY_TO_BOTH = "Valuation and Total"
APPLY_TO_OPTIONS = (APPLY_TO_NONE, APPLY_TO_TOTAL, APPLY_TO_VALUATION, APPLY_TO_BOTH)

# Categories involving stock valuation. Sales Taxes and Charges has no
# category field at all, so these are purchase-only by construction.
VALUATION_APPLY_TO = (APPLY_TO_VALUATION, APPLY_TO_BOTH)

DISTRIBUTE_BY_AMOUNT = "Amount"
DISTRIBUTE_BY_QUANTITY = "Quantity"

# Native ERPNext charge types the distribution basis maps onto.
#   Amount   -> "Actual": spread proportionally to item.net_amount
#   Quantity -> "On Item Quantity": rate per unit x item.qty
CHARGE_TYPE_BY_DISTRIBUTION = {
	DISTRIBUTE_BY_AMOUNT: "Actual",
	DISTRIBUTE_BY_QUANTITY: "On Item Quantity",
}

TAX_ROW_MARKER = "custom_neocharge_row"

CHARGE_BASIS_AMOUNT = "Amount"
CHARGE_BASIS_PERCENTAGE = "Percentage"

# Fields holding the source document's own party control account. Charges must
# never post to these in inline mode — it corrupts invoice reconciliation.
PARTY_CONTROL_FIELD = {
	"Sales Invoice": "debit_to",
	"Purchase Invoice": "credit_to",
}

SETTINGS_DOCTYPE = "NeoCharges Settings"
CHARGE_ROW_DOCTYPE = "NeoCharge Line"
TEMPLATE_DOCTYPE = "NeoCharge Template"

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

# ---------------------------------------------------------------------------
# Item discounts (v2.0)
# ---------------------------------------------------------------------------
# Item-wise discounts are a pricing feature, not a posting feature, so they
# reach POS Invoice as well as the charge doctypes. Every value they produce is
# written into ERPNext's own item fields (price_list_rate, discount_amount,
# rate), which is what makes them visible to ERPNext's discount accounting and
# to ZATCA XML builders without either knowing this app exists.
DISCOUNT_SALES_DOCTYPES = SALES_DOCTYPES + ("POS Invoice",)
DISCOUNT_DOCTYPES = DISCOUNT_SALES_DOCTYPES + PURCHASE_DOCTYPES

ITEM_DOCTYPE_BY_PARENT = {
	"Quotation": "Quotation Item",
	"Sales Order": "Sales Order Item",
	"Delivery Note": "Delivery Note Item",
	"Sales Invoice": "Sales Invoice Item",
	"POS Invoice": "POS Invoice Item",
	"Supplier Quotation": "Supplier Quotation Item",
	"Purchase Order": "Purchase Order Item",
	"Purchase Receipt": "Purchase Receipt Item",
	"Purchase Invoice": "Purchase Invoice Item",
}

# Item row fields
ND_GROSS = "custom_nd_gross_rate"
ND_DISC_1 = "custom_nd_discount_1"
ND_DISC_2 = "custom_nd_discount_2"
ND_EXTRA = "custom_nd_extra_discount"
ND_REASON = "custom_nd_reason"
ND_EFFECTIVE = "custom_nd_effective_pct"
ND_UNIT = "custom_nd_unit_discount"
ND_LINE = "custom_nd_line_discount"
ND_ACTIVE = "custom_nd_active"

# Parent fields
ND_TOTAL = "custom_nd_total_discount"
ND_ZATCA_FLAG_SET = "custom_nd_zatca_flag_set"
ND_ZATCA_REASON_SET = "custom_nd_zatca_reason_set"

ROUNDING_ZATCA = "ZATCA-safe (2 decimals)"
ROUNDING_DOCUMENT = "Document precision"

AUTHORITY_SELLING = "Selling documents"
AUTHORITY_ALL = "All documents"

MIXED_REASON_LARGEST = "Use the reason with the largest discount"
MIXED_REASON_BLOCK = "Block saving"

REASON_DOCTYPE = "NeoDiscount Reason"
DISCOUNT_CONFIG_DOCTYPE = "NeoDiscount DocType Config"
AUTHORITY_DOCTYPE = "NeoDiscount Authority"

# ZATCA documents and the field convention ZATCA e-invoicing apps on ERPNext
# use for price-level line discounts. Overridable in Settings for apps that
# renamed them.
ZATCA_DOCTYPES = ("Sales Invoice", "POS Invoice")
ZATCA_FLAG_FIELD = "custom_submit_line_item_discount_to_zatca"
ZATCA_REASON_CODE_FIELD = "custom_zatca_discount_reason_code"
ZATCA_REASON_FIELD = "custom_zatca_discount_reason"
ZATCA_NOMINAL_FIELD = "custom_zatca_nominal_invoice"

# ---------------------------------------------------------------------------
# Discount methods (v2.1)
# ---------------------------------------------------------------------------
# Keep Rate: Rate and Amount stay as entered. The discount reduces the line's
# net amount through ERPNext's own per-item discount field
# (mapped_additional_discount_amount, ERPNext v16), so VAT, income and the
# document total come out discounted while the visible line value does not
# change. Reduce Rate: the 2.0 behaviour, the discount is taken off Rate.
METHOD_KEEP = "Keep Rate (discount on net amount)"
METHOD_REDUCE = "Reduce Rate"
DISCOUNT_METHODS = (METHOD_KEEP, METHOD_REDUCE)

MODE_KEEP = "Keep Rate"
MODE_REDUCE = "Reduce Rate"

MAPPED_DISCOUNT_FIELD = "mapped_additional_discount_amount"
DISTRIBUTED_DISCOUNT_FIELD = "distributed_discount_amount"

ND_LINE_AMOUNT = "custom_nd_line_amount"
ND_NET = "custom_nd_net_rate"
ND_MODE = "custom_nd_mode"

# ---------------------------------------------------------------------------
# Charge currencies (v2.3)
# ---------------------------------------------------------------------------
# Every charge line carries its own currency. `amount` is in that currency,
# `exchange_rate` converts it to company currency, `base_amount` is the
# company-currency value that posts. A USD invoice can therefore carry USD
# freight and SAR customs side by side, each kept in its own currency.
CURRENCY_BASIS_COMPANY = "Company Currency"
CURRENCY_BASIS_DOCUMENT = "Document Currency"
CURRENCY_BASIS_SPECIFIC = "Specific Currency"
CURRENCY_BASES = (CURRENCY_BASIS_COMPANY, CURRENCY_BASIS_DOCUMENT, CURRENCY_BASIS_SPECIFIC)

# Percentage rows are computed on document totals, in document currency.
DOC_FIELD_BY_BASIS = {
	"Net Total": "net_total",
	"Grand Total": "grand_total",
	"Total Taxes and Charges": "total_taxes_and_charges",
}

CHARGE_TOTAL_FIELD = "custom_neocharge_total"

# Changelog

## 2.0.0 — 2026-11-03

### Added
- Item discounts on Quotation, Sales Order, Delivery Note, Sales Invoice, POS
  Invoice and the buying chain: Discount 1 %, cascading Discount 2 %, extra
  amount per unit, reason, computed effective %, discount per unit and line
  discount; document total of item discounts.
- Results written into ERPNext's native price list rate, discount amount and
  rate, so totals, VAT, returns, discount accounting and ZATCA XML builders
  pick them up unchanged.
- ZATCA-safe rounding (default): gross, discount and net at two decimals with
  gross = net + discount exact.
- NeoDiscount Reason with UNTDID 5189 codes (19 seeded, English and Arabic),
  optional maximum %, and per-company discount accounts.
- Discount authority by role, enforced at submit.
- Item → Max Discount enforced against the effective discount.
- ZATCA layer: sets the invoice line-discount flag and reason fields, blocks
  nominal invoices with line discounts and discounts without a reason code,
  warns on VAT-inclusive rounding drift and on POS Invoices whose Company flag
  is off. Configurable field names.
- ZATCA Discount Preview dialog on Sales and POS Invoices.
- `neocharges.discounts.zatca.get_line_allowances()` for ZATCA builders that
  want per-line reasons.
- Guard against Add-To charge rows on Sales Invoices when a ZATCA app is
  installed.
- Unit tests for the discount arithmetic; verify_tree checks for the discount
  module's design constraints.

### Fixed
- Removing every charge line left the app's tax rows on the document.
- Payment schedule and in-words fields were not refreshed after the app
  changed the taxes table.

### Changed
- Frappe/ERPNext range widened to v15 and v16 (`>=15.0.0,<17.0.0`).
- New module `Discounts`; settings page reorganised into Additional Charges,
  Item Discounts, Discount Authority and ZATCA sections.

# NeoCharges

Two features for ERPNext sales and purchase documents:

- **Additional charges**: debit/credit charge lines that post an independent,
  self-balancing Journal Entry and **never** touch the source document's net
  total, tax total or grand total (or, by choice, flow into the document's own
  taxes table).
- **Item discounts** (new in 2.0): two cascading percentages and an extra
  amount per unit on every item row, with reason codes, authority limits and
  ZATCA-compatible output.

Version 2.1.0 · Frappe/ERPNext v15 and v16 · Neotec Integrated Solutions

ZATCA's e-invoice models discounts and charges as the same element
(`AllowanceCharge`, with `ChargeIndicator` false or true), which is why both
live in one app.

---

## Additional charges

Add lines like *Transport charges — debit Transport Expenses, credit Transport
Payables — 1,500* to a Purchase Invoice. On submit the app creates one Journal
Entry containing a debit row and a credit row per line, tagged with the source
document. The supplier still owes only the invoice value.

Because the charge table lives outside the standard `taxes` table, it cannot
affect totals by construction — there is no calculation to get wrong.

## Posting modes

Set per document type in **NeoCharges Settings → Posting Mode**.

**Journal Entry** (default) — creates a separate, self-balancing JE referencing
the source document. The invoice ledger stays exactly as ERPNext wrote it.

**Inline with Document GL** — appends the debit and credit rows to the source
document's own ledger, so they appear in the General Ledger under the invoice's
voucher number alongside its normal entries. The pair balances, so the voucher's
own debit/credit check still passes. `update_outstanding` is off and no
`against_voucher` is written, so the invoice's outstanding amount is untouched.

Inline mode is only selectable on document types that post GL of their own
(Sales Invoice, Delivery Note, Purchase Invoice, Purchase Receipt). Settings
rejects it elsewhere.

### Posting controls

Inline entries are built with the source document's own `get_gl_dict()` and
written through ERPNext's `make_gl_entries()`. Everything ERPNext applies to
its own ledger entries therefore applies to charge rows too:

| Control | Where it comes from |
|---|---|
| Closed Accounting Period | `validate_accounting_period` |
| Fiscal year resolution / multiple fiscal years | `get_gl_dict`, `validate_and_set_fiscal_year` |
| Books-closed date (Accounts Settings) | `check_freezing_date` |
| Period Closing Voucher guard | `validate_against_pcv` |
| Budget control (action on exceeding) | `validate_expense_against_budget` |
| Disabled, group and frozen accounts | `validate_disabled_accounts`, `validate_frozen_account`, `validate_account_details` |
| Cost center mandatory on P&L | `pl_must_have_cost_center` |
| Account/company and currency checks | `check_pl_account`, `validate_currency` |
| Accounting dimensions, incl. mandatory-for-P&L/BS | `get_gl_dict`, `validate_dimensions_for_pl_and_bs` |
| Automatic dimension offsetting entries | `make_acc_dimensions_offsetting_entry` |
| Cost center allocation | `distribute_gl_based_on_cost_center_allocation` |
| Payment Ledger Entries for party rows | `create_payment_ledger_entry` |
| Reversal on cancel, immutable ledger | `make_reverse_gl_entries` |

Dimensions the charge row leaves blank are inherited from the source document,
exactly as an item row's would be. Cost center falls back through the row, the
NeoCharges default, the document, then the company default.

Journal Entry mode inherits the same controls through the Journal Entry's own
submit path.

### Before you choose inline

Three consequences, none of them blockers, all of them worth knowing:

1. **Repost.** "Repost Accounting Ledger" regenerates the voucher's GL from
   the document, which knows nothing about charge lines. NeoCharges hooks that
   document's submit and re-applies the inline entries afterwards, skipping any
   voucher whose charge accounts already appear in the reposted ledger (adding
   a second copy would double-post). Reposts triggered by other paths — a
   backdated stock entry, an exchange-rate revaluation — do not pass through
   that hook, so verify the ledger after one. Journal Entry mode has no such
   exposure at all.
2. **The voucher no longer ties to the document.** The invoice's ledger total
   will not equal its grand total. Auditors reconciling GL to document face
   value will find a difference they cannot explain from the invoice print.
3. **The source document's own control account is blocked.** Posting a charge
   to the invoice's own `debit_to` / `credit_to` would corrupt reconciliation,
   so validation rejects it. Use a different account or switch to JE mode.

If any of those matter to you, keep Journal Entry mode. Inline is the right
choice when the priority is a single, readable ledger per voucher.

## Percentage charges

Each charge line has a **Basis**: `Amount` or `Percentage`.

On percentage rows, set the rate and pick what it applies to — Net Total,
Grand Total, or Total Taxes and Charges — all read from the document's
company-currency totals. The Amount field becomes read-only and shows the
computed figure.

The form previews the value as you type, but the server recomputes every
percentage row on validate and **that** is what posts. Editing totals after
entering a charge cannot leave a stale amount behind.

Templates carry the basis too, so a "Reseller Commission — 15% of Net Total"
template stays correct across invoices of different sizes.

## Add To / Distribute Based On

Two columns decide whether a charge stays a memo or becomes part of the
document's own value.

| Add To | Effect |
|---|---|
| `None` (default) | Standalone memo pair. Totals untouched. Posts via Journal Entry or inline GL, as before. |
| `Total` | Adds to the document's grand total. |
| `Valuation` | Loads onto item valuation. Purchase documents only. |
| `Valuation and Total` | Both. |

Anything other than `None` writes the charge into the document's **native Taxes
and Charges table** as an ERPNext tax row, and NeoCharges then posts nothing of
its own — ERPNext's own accounting carries it. That is deliberate: ERPNext
already knows how to add a charge to a total, load it onto valuation, and carry
it through returns, reposts, perpetual inventory and stock reconciliation.
Reimplementing any of that would fight the framework at every turn.

The row is tagged with a hidden marker so the app only ever touches rows it
created and leaves your own tax table alone. Clear the charge line or switch it
back to `None` and the tax row is removed.

**Distribute Based On** maps onto native charge types:

| Basis | Charge type | Spread |
|---|---|---|
| `Amount` | `Actual` | Proportional to each item's net amount |
| `Quantity` | `On Item Quantity` | Per-unit rate multiplied by each item's qty |

For a 1,500 charge over items of 6,000/2,000/2,000 net and 3/7/10 qty: Amount
gives 900/300/300, Quantity gives 225/525/750. Both reconcile to 1,500.

### What changes in this mode

- **Debit and credit accounts no longer apply.** A tax row has one Account
  Head; the counter leg is chosen by ERPNext from the category — debited
  against the party for `Total`, credited against stock for `Valuation`. The
  memo-mode account and party fields are cleared when you switch.
- **Sales documents cannot use valuation.** Sales Taxes and Charges has no
  category field in ERPNext. Validation rejects it rather than failing later.
- **Percentage of Grand Total is rejected** for these rows — the charge would
  change the figure it is computed from. Use Net Total.
- **Totals are recalculated** after the rows are synced, because the app's
  validate hook runs after the controller has already totalled the document.
- Quantity distribution needs a total item quantity above zero.

## Party support

Payable and Receivable accounts make party mandatory in ERPNext. The app
handles this rather than forcing you to work around it:

- Pick a Payable or Receivable account on either leg and the row demands a
  Party Type and Party, with the party type pre-filled from the account type.
- The party is attached only to the leg that carries the party account. The
  other leg posts clean.
- If both legs are party accounts the row is rejected — that needs two lines,
  one per party.
- On non-party accounts, any stray party data is stripped before posting.

No `reference_type` is set on the Journal Entry rows, so the resulting balance
sits as an open item against that party and settles through a normal Payment
Entry.

## Setup

1. `bench get-app neocharges <repo-url>` then `bench --site <site> install-app neocharges`
   (on Frappe Cloud: add to the bench group, push, deploy).

   `pyproject.toml` declares `[tool.bench.frappe-dependencies]` with
   `frappe = ">=15.0.0,<17.0.0"`. Frappe Cloud validates this against the bench
   group's Frappe version and rejects the branch on a mismatch. `verify_tree.py`
   fails the build if the section goes missing or stops covering v16.
2. Open **NeoCharges Settings**.
3. Tick the document types you want. Custom fields are created the moment you
   save — no separate migrate step.

Enabled by default: Sales Invoice, Delivery Note, Purchase Invoice, Purchase
Receipt. Listed but off: Quotation, Sales Order, Supplier Quotation, Purchase
Order — see *Duplicate posting* below.

## Settings

| Field | Effect |
|---|---|
| Enable Additional Charges | Master switch; off means nothing posts anywhere |
| Require Cost Center | Enforces a cost center on income/expense charge accounts |
| Default Cost Center | Fallback when the row and document both leave it blank |
| Enabled Document Types | Per-doctype switches |

Per document type:

| Field | Effect |
|---|---|
| Enabled | Adds the charge fields to that doctype |
| Auto Apply | Pre-ticks "Apply Additional Charges" on new documents |
| Auto Submit JE | Off leaves the Journal Entry in draft for review |
| Default Template | Pre-loads a charge template on new documents |

## Templates

**NeoCharge Template** stores reusable line sets per company, scoped to
Sales, Purchase or Both. Selecting one on a transaction replaces the charge
table with its lines; amounts remain editable.

## Duplicate posting

Quotation, Sales Order, Supplier Quotation and Purchase Order post no GL
entries of their own. Charges raised there create a standalone accrual, which
is legitimate but means the charge exists in the ledger before the invoice.
If the same charge is then entered on the downstream invoice, it posts twice —
nothing links the two documents' charge tables.

The app guards against re-posting the *same* document (a second submit finds
the existing Journal Entry and stops) but it cannot detect the same charge
entered on a different document. Decide one stage and stay there. That is why
the order and quotation doctypes ship disabled.

## Behaviour notes

- Amounts are always in **company currency**. Where the target account carries
  a different currency, the account-currency amount is derived using the
  exchange rate on the posting date.
- Cancelling the source document cancels the charge Journal Entry. A draft JE
  is deleted instead.
- The JE link field is stored as **Data**, not Link, deliberately: a Link would
  trigger the linked-submitted-document check and block cancellation of the
  source document.
- Group, disabled and frozen accounts are rejected at validation, as are
  accounts belonging to another company.

## Item discounts

Each item row gets an **Item Discount** section:

| Field | Meaning |
|---|---|
| Gross Rate | Rate before discount, filled from the row automatically. |
| Discount 1 (%) | First discount, on the gross rate. |
| Discount 2 (%) | Second discount, on the balance after Discount 1. 10% + 5% is 14.5% effective. |
| Extra Discount per Unit | Fixed amount per unit, after both percentages. |
| Discount Amount (Line) | Fixed amount off the whole line, after the other discounts. |
| Discount Reason | Reason with a ZATCA reason code. Required by default. |
| Effective Discount (%), Discount per Unit, Net Rate after Discount, Line Discount | Computed, read-only. |

The user enters only the discount, as a percentage or an amount. The
document gets a read-only **Total Item Discount**.

### Discount method

Chosen in **NeoCharges Settings → Discount Method**.

**Keep Rate (discount on net amount)**, the default on ERPNext v16. Rate and
Amount stay exactly as entered. Each line's discount is written into ERPNext's
own per-item discount field (`mapped_additional_discount_amount`), and ERPNext
then:

- lowers that line's net amount and net rate,
- calculates VAT on the discounted net amount,
- posts that line's income at the discounted figure, to that line's own
  income account,
- shows the total as the document's **Additional Discount** on Net Total.

Nothing is posted separately. Example, 15% VAT:

| Line | Qty | Rate | Amount | Discount | Net amount |
|---|---|---|---|---|---|
| Subscription | 1 | 120,000.00 | 120,000.00 | 10% = 12,000.00 | 108,000.00 |
| Camera & Training | 2 | 2,700.00 | 5,400.00 | 400.00 (line) | 5,000.00 |

Total 125,400.00 · Discount 12,400.00 · Net Total 113,000.00 · VAT 16,950.00
· Grand Total 129,950.00.

A line amount is exact in this method (400 over 2 units is 400), because the
discount is applied to the line, not squeezed into a per-unit rate.

Keep Rate cannot be combined with a manually entered Additional Discount on
the same document: ERPNext clears per-item discounts whenever the header
discount is edited by hand. Saving such a document stops with a message.
Pricing rules and margins are fine in this method, because the rate they
produce is simply the gross.

Keep Rate needs ERPNext v16. On v15 Settings refuses it and Reduce Rate is
used.

**Reduce Rate.** The discount is taken off the rate:

| Native field | Value |
|---|---|
| `price_list_rate` | gross rate |
| `discount_percentage` | 0 |
| `discount_amount` | discount per unit |
| `rate` | net rate |

`discount_percentage` is deliberately zero. ERPNext v15 recomputes `rate` from
a non-zero percentage when pricing rules are on the row, and v16 resets the
discount whenever the percentage and rate disagree by a rounding cent. A
simulation of both versions' item-rate logic over 100,000 random lines found
no case where the rate moved. A line amount becomes a per-unit figure, so a
total that does not divide evenly by quantity lands on the nearest cent per
unit; the form says so.

Switching method on a draft is safe: lines discounted under the other method
convert on the next save. Submitted documents are never touched.

### Timing

Discounts apply in `before_validate`, ahead of the controller's own validate,
so ERPNext totals the document, builds the payment schedule and runs its
checks from the discounted figures in the normal way. `validate` then
verifies that nothing in between moved a line, and stops the save with a
clear message if something did.

### Rounding (Reduce Rate)

**ZATCA-safe (2 decimals)** (default) rounds gross and net to two decimals
and derives the discount from them, so `gross = net + discount` holds exactly.
The e-invoice prints those three figures as BaseAmount, PriceAmount and
Allowance Amount, and they reconcile by construction. **Document precision**
uses the rate field's precision instead, for non-ZATCA use.

### Rules

- **Pricing rules and margins (Reduce Rate only).** A line priced by a
  pricing rule cannot also take an item discount unless the document has
  *Ignore Pricing Rule* ticked, and a line carries a margin or an item
  discount, not both. ERPNext re-applies both during validate.
- **Additional Discount (Keep Rate only).** Cannot be combined with item
  discounts on the same document.
- **Free items** from pricing rules cannot be discounted.
- **Item → Max Discount** is enforced against the effective discount on
  selling documents. ERPNext checks `discount_percentage`, which this app keeps
  at zero, so the app runs the check itself.
- **Reason cap.** A reason can carry a maximum discount percentage.
- **100% discounts** are rejected unless *Allow 100% Line Discount* is on.
- Clearing all three inputs restores the gross rate on the next save.

### Discount authority

Fill **Limits by Role** in Settings to enable it. Drafts save freely. To
submit, the submitting user needs a role whose limit covers every discounted
line, so a salesperson prepares and a manager submits. Returns are exempt.
*Apply Authority To* chooses selling documents only or all documents.

### Discount accounting per reason

Turn on **Selling Settings → Enable Discount Accounting** and add a discount
account per company on each Discount Reason. ERPNext then posts income at the
gross figure and the discount to that account, inside the invoice's own entry.

- Keep Rate: the app fills the invoice's empty *Additional Discount Account*
  when all discounted lines point to one account.
- Reduce Rate: the app fills each item row's empty *Discount Account*.

### Document types

Enabled by default: Quotation, Sales Order, Delivery Note, Sales Invoice. The
discount fields carry through *Make → Sales Order / Delivery Note / Invoice*
and into returns. Listed but off: POS Invoice and the buying chain.

POS Invoice support covers the desk form and POS front-ends that write the
discount fields through the API. The standard point-of-sale screen does not
show item custom fields.

## ZATCA

The ZATCA layer acts on Sales Invoice and POS Invoice when *ZATCA
Integration* is on and a ZATCA app, or its fields, is present on the site.

### Keep Rate on the e-invoice

Each line is printed at its entered price and amount. The discount total is
the invoice's document-level allowance, which ZATCA apps on ERPNext build from
the Additional Discount and the invoice-level reason fields. The app fills the
reason fields, leaves the line-discount flag off, and blocks:

- nominal invoices with item discounts;
- invoices that mix VAT categories. ZATCA apps place a document discount in
  a single VAT category and reject invoices that combine one with several.
  Split such an invoice, or use Reduce Rate for it.

### Reduce Rate on the e-invoice

ZATCA apps on ERPNext build each line's price block from the native fields:

```
cac:Price/cbc:PriceAmount                      item.rate
cac:Price/cac:AllowanceCharge/cbc:Amount       item.discount_amount
cac:Price/cac:AllowanceCharge/cbc:BaseAmount   item.rate + item.discount_amount
.../cbc:AllowanceChargeReasonCode, Reason      invoice-level reason fields
```

and they emit the AllowanceCharge block only when the invoice's line-discount
flag is ticked. On save the app:

1. ticks that flag on invoices that carry item discounts, and unticks it
   later only if the app was the one that ticked it;
2. fills the invoice-level reason code and text from the line reasons;
3. blocks nominal invoices with line discounts, which ZATCA rejects;
4. blocks a discount with no reason code;
5. warns when VAT-inclusive pricing will make the e-invoice's own rounding
   drift by a cent.

The XML itself is never touched. It is built and signed by the ZATCA app,
and editing it afterwards would break the invoice hash. `verify_tree.py`
fails the build if XML handling appears in the discount module.

### Field convention

Defaults follow the common convention and can be changed in Settings if the
installed ZATCA app renamed them:

| Setting | Default |
|---|---|
| Line Discount Flag Field | `custom_submit_line_item_discount_to_zatca` |
| Reason Code Field | `custom_zatca_discount_reason_code` |
| Reason Text Field | `custom_zatca_discount_reason` |

On POS Invoices the flag lives on Company. The app warns when a POS Invoice
carries item discounts and the Company flag is off.

### One reason per invoice

The invoice-level fields carry a single reason. With several reasons on one
invoice, *Several Reasons on One Invoice* either uses the reason carrying the
largest discount, or blocks the save. Lines sharing a code and text count as
one reason. *Reason Text Language* chooses English or Arabic text.

Reasons ship pre-seeded with the UNTDID 5189 allowance codes ZATCA validates
against (95 Discount, 100 Special rebate, 104 Standard and the rest), with
English and Arabic text. Default reason: *95 - Discount*.

### VAT-inclusive pricing

With *Included in Print Rate* on the VAT row, the e-invoice divides price,
discount and base by (1 + VAT) and rounds each separately. They can then miss
each other by 0.01. The app cannot fix that without changing the price, so it
warns on save and names the rows. Adjust the gross rate or discount slightly,
or price those lines VAT-exclusive.

### ZATCA Discount Preview

On a saved Sales or POS Invoice with item discounts, **View → ZATCA Discount
Preview** shows each line's base amount, price allowance, price, line total
and item discount as the e-invoice will print them (VAT-exclusive), plus the
document-level allowance under Keep Rate, with a check column.

### Tax-table charges on ZATCA invoices

ZATCA apps read the Sales Taxes and Charges table as VAT, and take the first
row's rate as the invoice VAT rate. A charge line with *Add To* other than
`None` writes a row into that table, so on a ZATCA site the e-invoice would
report the charge as tax. *Block Tax-Table Charges on ZATCA Invoices* (on by
default) rejects those rows on Sales Invoices. Memo-mode charges (`Add To:
None`) post their own entry, never touch the invoice, and are unaffected.

### Integration API

For a ZATCA app that wants per-line reasons instead of one invoice reason:

```python
from neocharges.discounts.zatca import get_line_allowances

data = get_line_allowances(sales_invoice_doc)
for line in data["lines"]:
    line["price_amount"], line["allowance_amount"], line["base_amount"]
    line["reason_code"], line["reason"], line["checks_ok"]
```

Amounts are VAT-exclusive, two decimals, in document currency. Works for
discounts typed into ERPNext's own fields too.

## Upgrading from 1.x

`bench migrate` runs `v2_0.setup_item_discounts`. It seeds the reasons, turns
item discounts on for the selling chain, sets *95 - Discount* as the default
reason and adds the fields. Additional charges keep their settings unchanged.

Two fixes in charge handling come with 2.0:

- Removing every charge line now also removes the tax rows the app had added
  to the taxes table. Before, they stayed behind.
- After the app adds or removes tax rows, the payment schedule and in-words
  fields are recalculated. A single-pass save-and-submit (API, data import)
  could otherwise store a schedule that no longer matched the grand total.

## Upgrading from 2.0

`bench migrate` runs `v2_1.discount_method`. On ERPNext v16 it sets the method
to Keep Rate; on v15, Reduce Rate. It adds the Line amount, Net Rate and
method fields. Drafts discounted under 2.0 switch over on their next save.

## Tests

The discount arithmetic is pure Python and runs without a site:

```
python3 -m unittest neocharges.tests.test_discount_calc
```

It covers both methods: cascading percentages, line amounts, rounding,
returns and the error cases; the `gross = net + discount` invariant over
20,000 random lines; and, for Keep Rate, that ERPNext's per-stock-unit storage
at 9 decimals gives back the exact line discount over 20,000 random lines and
units of measure.

## Files

```
neocharges/
├── constants.py                  supported doctypes, field names
├── hooks.py                      doc_events: discounts first, then charges
├── install.py                    idempotent seeders (install + migrate)
├── api.py                        whitelisted endpoints for the form scripts
├── charges/
│   ├── posting.py                validation, JE build, cancellation
│   ├── native_taxes.py           Add To rows in the native taxes table
│   ├── custom_fields.py          idempotent field seeding
│   └── doctype/                  5 doctypes
├── discounts/
│   ├── calc.py                   pure arithmetic for both methods, no Frappe
│   ├── engine.py                 Keep Rate / Reduce Rate, verify, authority
│   ├── zatca.py                  flag, reason, guards, preview, API
│   ├── custom_fields.py          item and document fields
│   ├── reasons.py                UNTDID 5189 reason seed
│   └── doctype/                  4 doctypes
├── patches/v2_0/, v2_1/          upgrades from 1.x and 2.0
├── tests/test_discount_calc.py   runs without a site
├── public/js/charges.js          charge form behaviour
├── public/js/discounts.js        discount preview and ZATCA dialog
└── translations/ar.csv           Arabic
verify_tree.py                    structural guard — run before commit
```

## Uninstall

`before_uninstall` removes charge fields only from document types with no
charge history, and discount fields only from item tables where no line ever
carried a discount. Anything that has posted keeps its fields so the audit trail
stays readable.

## Note on naming

Released as **NeoCharges**. If a pre-release build under the previous package
name was ever installed on a site, uninstall it before installing this one —
the doctype names and custom fieldnames both changed, so the two do not share
data and running them side by side would duplicate the fields on every enabled
document type.

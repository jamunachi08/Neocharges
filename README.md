# NeoCharges

Debit/credit charge lines on sales and purchase documents that post an
independent, self-balancing Journal Entry and **never** touch the source
document's net total, tax total or grand total.

Version 1.2.0 · Frappe/ERPNext v15 · Neotec Integrated Solutions

---

## What it does

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
   `frappe = ">=15.0.0,<16.0.0"`. Frappe Cloud validates this against the bench
   group's Frappe version and rejects the branch on a mismatch — widen or move
   the range if you deploy to a v16 group. `verify_tree.py` fails the build if
   the section goes missing.
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

## Files

```
neocharges/
├── constants.py                  supported doctypes, field names
├── hooks.py                      doc_events on the supported list only
├── install.py                    idempotent seeders (install + migrate)
├── api.py                        whitelisted endpoints for the form script
├── charges/
│   ├── posting.py                validation, JE build, cancellation
│   ├── custom_fields.py          idempotent field seeding
│   └── doctype/                  5 doctypes
├── public/js/charges.js          form behaviour, all doctypes
└── translations/ar.csv           Arabic
verify_tree.py                    structural guard — run before commit
```

## Uninstall

`before_uninstall` removes charge fields only from document types with no
charge history. Anything that has posted keeps its fields so the audit trail
stays readable.

## Note on naming

Released as **NeoCharges**. If a pre-release build under the previous package
name was ever installed on a site, uninstall it before installing this one —
the doctype names and custom fieldnames both changed, so the two do not share
data and running them side by side would duplicate the fields on every enabled
document type.

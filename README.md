# NeoCharges

Debit/credit charge lines on sales and purchase documents that post an
independent, self-balancing Journal Entry and **never** touch the source
document's net total, tax total or grand total.

Version 1.0.0 · Frappe/ERPNext v15 · Neotec Integrated Solutions

---

## What it does

Add lines like *Transport charges — debit Transport Expenses, credit Transport
Payables — 1,500* to a Purchase Invoice. On submit the app creates one Journal
Entry containing a debit row and a credit row per line, tagged with the source
document. The supplier still owes only the invoice value.

Because the charge table lives outside the standard `taxes` table, it cannot
affect totals by construction — there is no calculation to get wrong.

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

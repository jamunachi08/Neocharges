"""Line discount arithmetic.

Pure Python with no Frappe import, so it can be unit-tested anywhere
(`python3 -m unittest neocharges.tests.test_discount_calc`). The caller passes
the rounding function, which in production is `frappe.utils.flt` so the
results round exactly the way ERPNext rounds everything else on the document.

The single invariant everything downstream depends on:

    gross == net + unit_discount        (exactly, at the chosen precision)

ERPNext stores that as price_list_rate == rate + discount_amount, and ZATCA
XML builders print PriceAmount = rate, AllowanceCharge/Amount =
discount_amount and BaseAmount = rate + discount_amount. If the invariant
holds, those three figures reconcile on the XML by construction.
"""

from dataclasses import dataclass

ERR_GROSS = "gross"
ERR_PERCENT = "percent"
ERR_EXTRA = "extra"
ERR_NEGATIVE = "negative"
ERR_FULL = "full"


class DiscountError(ValueError):
	def __init__(self, code, **context):
		super().__init__(code)
		self.code = code
		self.context = context


@dataclass(frozen=True)
class LineResult:
	gross: float
	net: float
	unit_discount: float
	effective_pct: float
	line_discount: float


def has_input(pct1, pct2, extra):
	return bool(pct1 or pct2 or extra)


def compute_line(
	gross,
	qty,
	pct1=0.0,
	pct2=0.0,
	extra=0.0,
	*,
	rnd,
	rate_precision=2,
	amount_precision=2,
	allow_full=False,
):
	"""Resolve one item row.

	pct1   first discount, on the gross rate
	pct2   second discount, on what is left after pct1 (cascading, 10% + 5%
	       is 14.5% effective, not 15%)
	extra  fixed amount per unit, taken after both percentages
	"""
	gross = float(gross or 0)
	pct1 = float(pct1 or 0)
	pct2 = float(pct2 or 0)
	extra = float(extra or 0)
	qty = float(qty or 0)

	if gross <= 0:
		raise DiscountError(ERR_GROSS)
	for value in (pct1, pct2):
		if value < 0 or value > 100:
			raise DiscountError(ERR_PERCENT, value=value)
	if extra < 0:
		raise DiscountError(ERR_EXTRA, value=extra)

	gross = rnd(gross, rate_precision)
	remaining = gross * (1.0 - pct1 / 100.0) * (1.0 - pct2 / 100.0) - extra
	net = rnd(remaining, rate_precision)

	if net < 0:
		raise DiscountError(ERR_NEGATIVE, gross=gross, net=net)
	if net == 0 and not allow_full:
		raise DiscountError(ERR_FULL)

	# Derive the discount from the two rounded figures rather than rounding it
	# independently: that is what keeps gross == net + discount exact.
	unit = rnd(gross - net, rate_precision)
	effective = rnd(unit / gross * 100.0, 3) if gross else 0.0
	line = rnd(unit * qty, amount_precision)

	return LineResult(
		gross=gross,
		net=net,
		unit_discount=unit,
		effective_pct=effective,
		line_discount=line,
	)

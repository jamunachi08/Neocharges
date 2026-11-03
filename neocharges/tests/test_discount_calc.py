"""Unit tests for the pure discount arithmetic. No Frappe needed.

Run from the repo root:  python3 -m unittest neocharges.tests.test_discount_calc
"""

import importlib.util
import os
import random
import unittest
from decimal import ROUND_HALF_UP, Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
	"nd_calc", os.path.join(_HERE, "..", "discounts", "calc.py")
)
calc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calc)


def rnd(value, precision):
	quantum = Decimal(1).scaleb(-precision)
	return float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))


class TestComputeLine(unittest.TestCase):
	def test_cascading_percentages(self):
		r = calc.compute_line(100, 2, 10, 5, rnd=rnd)
		self.assertEqual(r.net, 85.5)
		self.assertEqual(r.unit_discount, 14.5)
		self.assertEqual(r.effective_pct, 14.5)
		self.assertEqual(r.line_discount, 29.0)

	def test_extra_amount_after_percentages(self):
		r = calc.compute_line(200, 1, 10, 0, 5, rnd=rnd)
		self.assertEqual(r.net, 175.0)
		self.assertEqual(r.unit_discount, 25.0)

	def test_awkward_rounding_keeps_invariant(self):
		r = calc.compute_line(9.99, 3, 14.5, rnd=rnd)
		self.assertEqual(rnd(r.net + r.unit_discount, 2), r.gross)

	def test_gross_is_rounded_to_rate_precision(self):
		r = calc.compute_line(10.005, 1, 10, rnd=rnd)
		self.assertEqual(r.gross, 10.01)

	def test_returns_produce_negative_line_discount(self):
		r = calc.compute_line(50, -2, 10, rnd=rnd)
		self.assertEqual(r.line_discount, -10.0)

	def test_rejects_discount_above_gross(self):
		with self.assertRaises(calc.DiscountError) as ctx:
			calc.compute_line(10, 1, 0, 0, 11, rnd=rnd)
		self.assertEqual(ctx.exception.code, calc.ERR_NEGATIVE)

	def test_full_discount_needs_permission(self):
		with self.assertRaises(calc.DiscountError) as ctx:
			calc.compute_line(10, 1, 100, rnd=rnd)
		self.assertEqual(ctx.exception.code, calc.ERR_FULL)
		r = calc.compute_line(10, 1, 100, rnd=rnd, allow_full=True)
		self.assertEqual(r.net, 0.0)
		self.assertEqual(r.unit_discount, 10.0)

	def test_rejects_bad_inputs(self):
		for kwargs, code in (
			({"gross": 0}, calc.ERR_GROSS),
			({"gross": 10, "pct1": 120}, calc.ERR_PERCENT),
			({"gross": 10, "pct2": -1}, calc.ERR_PERCENT),
			({"gross": 10, "extra": -1}, calc.ERR_EXTRA),
		):
			gross = kwargs.pop("gross")
			with self.assertRaises(calc.DiscountError) as ctx:
				calc.compute_line(gross, 1, rnd=rnd, **kwargs)
			self.assertEqual(ctx.exception.code, code)

	def test_invariant_holds_across_random_inputs(self):
		rng = random.Random(20261103)
		for _ in range(20000):
			gross = round(rng.uniform(0.01, 99999), rng.choice((2, 3, 4)))
			pct1 = round(rng.uniform(0, 60), rng.choice((0, 1, 2, 3)))
			pct2 = round(rng.uniform(0, 30), rng.choice((0, 1, 2)))
			extra = round(rng.uniform(0, gross * 0.2), 2) if rng.random() < 0.3 else 0
			qty = rng.choice((1, 2, 3, 7, 12.5, -4))
			for precision in (2, 3):
				try:
					r = calc.compute_line(
						gross, qty, pct1, pct2, extra, rnd=rnd, rate_precision=precision
					)
				except calc.DiscountError:
					continue
				self.assertEqual(rnd(r.net + r.unit_discount, precision), r.gross)
				self.assertGreaterEqual(r.net, 0)
				self.assertGreaterEqual(r.unit_discount, 0)


if __name__ == "__main__":
	unittest.main()

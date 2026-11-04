"""Minimal in-memory stand-in for the parts of Frappe the ZATCA check uses.

Lets the rule logic run under plain unittest. It is not a Frappe emulator:
only the calls the check makes are implemented.
"""

import datetime
import html
import sys
import types


class DoesNotExistError(Exception):
	pass


class Meta:
	def __init__(self, fields):
		self.fields = set(fields)

	def has_field(self, name):
		return name in self.fields


class Doc(dict):
	"""dict with attribute access, a meta, and child rows."""

	def __init__(self, doctype, fields=(), **values):
		super().__init__(values)
		self["doctype"] = doctype
		self.meta = Meta(set(fields) | set(values))

	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			return None

	def __setattr__(self, key, value):
		if key == "meta":
			object.__setattr__(self, key, value)
		else:
			self[key] = value

	def set(self, key, value):
		self[key] = value

	def precision(self, fieldname=None):
		return 2


class Store:
	def __init__(self):
		self.docs = {}
		self.apps = ["frappe", "erpnext"]
		self.metas = {}

	def add(self, doc):
		self.docs[(doc["doctype"], doc["name"])] = doc
		return doc

	def get(self, doctype, name):
		try:
			return self.docs[(doctype, name)]
		except KeyError:
			raise DoesNotExistError(f"{doctype} {name}") from None


STORE = Store()


def install():
	"""Install the stand-in, unless real Frappe is loaded (bench run-tests).

	Replacing the real module inside a bench process would break every other
	test in it, so this returns False there and the rule tests skip.
	"""
	real = sys.modules.get("frappe")
	if real is not None and hasattr(real, "local"):
		return False

	m = types.ModuleType("frappe")
	m.DoesNotExistError = DoesNotExistError
	m._ = lambda s: s
	m._dict = lambda *a, **k: types.SimpleNamespace(**(a[0] if a else {}), **k)

	def get_doc(doctype, name=None):
		return STORE.get(doctype, name)

	m.get_doc = get_doc
	m.get_cached_doc = get_doc
	m.get_cached_value = lambda doctype, name, field: STORE.get(doctype, name).get(field)
	m.log_error = lambda *a, **k: None
	m.get_traceback = lambda: ""
	m.get_installed_apps = lambda: list(STORE.apps)
	m.get_meta = lambda doctype: STORE.metas.get(doctype, Meta(()))

	def get_all(doctype, filters=None, pluck=None, **_):
		rows = [d for (dt, _n), d in STORE.docs.items() if dt == doctype]
		for key, value in (filters or {}).items():
			if isinstance(value, tuple) and value[0] == "in":
				rows = [d for d in rows if d.get(key) in value[1]]
			else:
				rows = [d for d in rows if d.get(key) == value]
		return [d[pluck] for d in rows] if pluck else rows

	m.get_all = get_all

	def throw(msg, title=None):
		raise RuntimeError(msg)

	m.throw = throw
	m.msgprint = lambda *a, **k: None
	m.db = types.SimpleNamespace(exists=lambda *a, **k: True, set_value=lambda *a, **k: None)

	utils = types.ModuleType("frappe.utils")

	def flt(value, precision=None):
		value = float(value or 0)
		return round(value, precision) if precision is not None else value

	utils.flt = flt
	utils.nowdate = lambda: datetime.date.today().isoformat()
	utils.cint = lambda v: int(float(v or 0))
	utils.getdate = lambda v: v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))
	utils.nowdate = lambda: datetime.date.today().isoformat()
	utils.now_datetime = datetime.datetime.now
	utils.escape_html = html.escape
	m.utils = utils

	sys.modules["frappe"] = m
	sys.modules["frappe.utils"] = utils
	return True

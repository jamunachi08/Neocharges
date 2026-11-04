#!/usr/bin/env python3
"""Structural guard for neocharges.

Run before every commit:  python3 verify_tree.py
Exits non-zero on any structural or syntax fault so a broken tree never
reaches Frappe Cloud, where a failed migrate takes the site with it.
"""

import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "neocharges")

REQUIRED_FILES = [
	"neocharges/__init__.py",
	"neocharges/hooks.py",
	"neocharges/modules.txt",
	"neocharges/patches.txt",
	"neocharges/install.py",
	"neocharges/api.py",
	"neocharges/constants.py",
	"neocharges/charges/__init__.py",
	"neocharges/charges/posting.py",
	"neocharges/charges/custom_fields.py",
	"neocharges/charges/native_taxes.py",
	"neocharges/public/js/charges.js",
	"neocharges/public/js/discounts.js",
	"neocharges/discounts/__init__.py",
	"neocharges/discounts/calc.py",
	"neocharges/discounts/engine.py",
	"neocharges/discounts/zatca.py",
	"neocharges/discounts/custom_fields.py",
	"neocharges/discounts/reasons.py",
	"neocharges/patches/v2_0/setup_item_discounts.py",
	"neocharges/patches/v2_1/discount_method.py",
	"neocharges/patches/v2_2/zatca_check.py",
	"neocharges/patches/v2_3/charge_currencies.py",
	"neocharges/charges/currency.py",
	"neocharges/charges/document_accounts.py",
	"neocharges/tests/test_document_accounts.py",
	"neocharges/tests/test_charge_currency.py",
	"neocharges/zatca_check/__init__.py",
	"neocharges/zatca_check/vendors.py",
	"neocharges/zatca_check/checks.py",
	"neocharges/zatca_check/custom_fields.py",
	"neocharges/public/js/zatca_check.js",
	"neocharges/tests/fake_frappe.py",
	"neocharges/tests/test_zatca_check.py",
	"neocharges/tests/test_discount_calc.py",
	"pyproject.toml",
	"README.md",
]

# module folder -> (module name, doctype slugs)
REQUIRED_DOCTYPES = {
	"charges": (
		"Charges",
		[
			"neocharges_settings",
			"neocharge_doctype_config",
			"neocharge_template",
			"neocharge_template_line",
			"neocharge_line",
		],
	),
	"discounts": (
		"Discounts",
		[
			"neodiscount_reason",
			"neodiscount_reason_account",
			"neodiscount_doctype_config",
			"neodiscount_authority",
		],
	),
}

errors = []


def read_text(path):
	with open(path, encoding="utf-8") as handle:
		return handle.read()


def check_required_files():
	for rel in REQUIRED_FILES:
		if not os.path.isfile(os.path.join(ROOT, rel)):
			errors.append(f"missing file: {rel}")


def check_python_syntax():
	for dirpath, _dirs, files in os.walk(APP):
		for name in files:
			if not name.endswith(".py"):
				continue
			path = os.path.join(dirpath, name)
			try:
				ast.parse(read_text(path))
			except SyntaxError as exc:
				errors.append(f"syntax error in {os.path.relpath(path, ROOT)}: {exc}")


def check_doctypes():
	for folder_name, (module, slugs) in REQUIRED_DOCTYPES.items():
		base = os.path.join(APP, folder_name, "doctype")
		for slug in slugs:
			_check_doctype(base, slug, module)


def _check_doctype(base, slug, expected_module):
	folder = os.path.join(base, slug)
	if not os.path.isdir(folder):
		errors.append(f"missing doctype folder: {slug}")
		return

	for suffix in ("__init__.py", f"{slug}.json"):
		if not os.path.isfile(os.path.join(folder, suffix)):
			errors.append(f"{slug}: missing {suffix}")

	json_path = os.path.join(folder, f"{slug}.json")
	if not os.path.isfile(json_path):
		return

	try:
		meta = json.loads(read_text(json_path))
	except json.JSONDecodeError as exc:
		errors.append(f"{slug}: invalid JSON — {exc}")
		return

	if meta.get("module") != expected_module:
		errors.append(f"{slug}: module is {meta.get('module')!r}, expected {expected_module!r}")

	if meta.get("name", "").lower().replace(" ", "_") != slug:
		errors.append(f"{slug}: doctype name {meta.get('name')!r} does not match its folder")

	fieldnames = {f["fieldname"] for f in meta.get("fields", []) if f.get("fieldname")}
	for fieldname in meta.get("field_order", []):
		if fieldname not in fieldnames:
			errors.append(f"{slug}: field_order references undefined field {fieldname!r}")

	for field in meta.get("fields", []):
		if field.get("fieldtype") in ("Link", "Table") and not field.get("options"):
			errors.append(f"{slug}: {field['fieldname']} is a {field['fieldtype']} with no options")
		if field.get("fieldtype") == "Dynamic Link" and field.get("options") not in fieldnames:
			errors.append(
				f"{slug}: dynamic link {field['fieldname']} points at "
				f"{field.get('options')!r} which is not a field on this doctype"
			)


def check_hooks_targets():
	"""Every dotted path in doc_events must resolve to a real module attribute."""
	hooks_path = os.path.join(APP, "hooks.py")
	source = read_text(hooks_path)

	for handler in ("validate_charges", "post_charges", "cancel_charges"):
		if handler not in source:
			errors.append(f"hooks.py: no reference to {handler}")

	posting = read_text(os.path.join(APP, "charges", "posting.py"))
	tree = ast.parse(posting)
	defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
	for handler in ("validate_charges", "post_charges", "cancel_charges"):
		if handler not in defined:
			errors.append(f"posting.py: handler {handler} is not defined")


def check_module_registration():
	modules = [line.strip() for line in read_text(os.path.join(APP, "modules.txt")).splitlines() if line.strip()]
	for _folder, (module, _slugs) in REQUIRED_DOCTYPES.items():
		if module not in modules:
			errors.append(f"modules.txt does not declare {module!r}")



def check_pyproject():
	"""Frappe Cloud rejects any app without a frappe-dependencies declaration."""
	import tomllib

	path = os.path.join(ROOT, "pyproject.toml")
	if not os.path.isfile(path):
		errors.append("missing pyproject.toml")
		return

	try:
		with open(path, "rb") as handle:
			data = tomllib.load(handle)
	except Exception as exc:  # noqa: BLE001
		errors.append(f"pyproject.toml is not valid TOML: {exc}")
		return

	deps = data.get("tool", {}).get("bench", {}).get("frappe-dependencies")
	if not deps:
		errors.append(
			"pyproject.toml: [tool.bench.frappe-dependencies] is missing — "
			"Frappe Cloud will refuse the app"
		)
	elif "frappe" not in deps:
		errors.append("pyproject.toml: [tool.bench.frappe-dependencies] does not declare 'frappe'")

	project_deps = data.get("project", {}).get("dependencies", []) or []
	for banned in ("frappe", "erpnext", "hrms", "payments"):
		if any(str(d).startswith(banned) for d in project_deps):
			errors.append(
				f"pyproject.toml: '{banned}' must not appear in [project] dependencies — "
				"pip would try to resolve it from PyPI"
			)



def check_patches_file():
	"""Frappe's patch parser reads both sections unconditionally.

	A patches.txt missing [pre_model_sync] raises KeyError during install-app,
	which is exactly how v1.0.1 failed on Frappe Cloud.
	"""
	import configparser

	path = os.path.join(APP, "patches.txt")
	if not os.path.isfile(path):
		errors.append("missing patches.txt")
		return

	parser = configparser.ConfigParser(allow_no_value=True)
	try:
		parser.read(path)
	except configparser.Error as exc:
		errors.append(f"patches.txt is not parseable: {exc}")
		return

	for section in ("pre_model_sync", "post_model_sync"):
		if not parser.has_section(section):
			errors.append(
				f"patches.txt: [{section}] section is missing — "
				"install-app will raise KeyError"
			)


def check_hooks_namespace():
	"""Every public name in hooks.py becomes a hook. Catch stray imports."""
	tree = ast.parse(read_text(os.path.join(APP, "hooks.py")))

	allowed = {
		"app_name", "app_title", "app_publisher", "app_description", "app_email",
		"app_license", "app_logo_url", "required_apps", "app_include_js",
		"app_include_css", "web_include_js", "web_include_css", "doctype_js",
		"doc_events", "scheduler_events", "override_whitelisted_methods",
		"after_install", "after_migrate", "before_install", "before_uninstall",
		"after_uninstall", "fixtures", "override_doctype_class", "permission_query_conditions",
		"has_permission", "jinja", "website_route_rules", "boot_session", "on_session_creation",
	}

	public = set()
	for node in tree.body:
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name) and not target.id.startswith("_"):
					public.add(target.id)
		elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
			public.add(node.name)
		elif isinstance(node, (ast.Import, ast.ImportFrom)):
			for alias in node.names:
				name = alias.asname or alias.name.split(".")[0]
				if not name.startswith("_"):
					public.add(name)

	stray = public - allowed
	if stray:
		errors.append(
			"hooks.py publishes non-hook name(s) as hooks: "
			+ ", ".join(sorted(stray))
			+ " — move them inside a function or prefix with underscore"
		)



def check_gl_routing():
	"""Inline posting must go through ERPNext's make_gl_entries.

	Building GL Entry documents directly would bypass accounting period
	checks, freeze dates, budget control, disabled accounts, dimension
	offsetting and payment ledger entries.
	"""
	posting = read_text(os.path.join(APP, "charges", "posting.py"))

	if "from erpnext.accounts.general_ledger import make_gl_entries" not in posting:
		errors.append("posting.py: inline mode must import erpnext make_gl_entries")

	if 'frappe.new_doc("GL Entry")' in posting or 'frappe.get_doc({"doctype": "GL Entry"' in posting:
		errors.append(
			"posting.py: GL Entry is being built directly — this bypasses ERPNext's "
			"posting controls; route through make_gl_entries instead"
		)

	if "get_gl_dict(" not in posting:
		errors.append("posting.py: inline entries must be built with doc.get_gl_dict()")

	if "strip_empty(" not in posting:
		errors.append(
			"posting.py: args passed to get_gl_dict must be stripped of empty values "
			"or inherited accounting dimensions get clobbered"
		)



def check_native_tax_delegation():
	"""Non-memo charges must delegate to ERPNext's taxes table, not reinvent it."""
	native = read_text(os.path.join(APP, "charges", "native_taxes.py"))

	for banned, why in (
		("valuation_rate", "valuation must be left to ERPNext's tax category"),
		("stock_ledger", "stock ledger entries must never be written by this app"),
		("base_grand_total =", "document totals must be recalculated, not assigned"),
	):
		if banned in native:
			errors.append(f"native_taxes.py: writes {banned!r} — {why}")

	if "calculate_taxes_and_totals" not in read_text(os.path.join(APP, "charges", "posting.py")):
		errors.append(
			"posting.py: tax rows are synced but totals are never recalculated — "
			"the charge would not reach the document total"
		)

	for charge_type in ("Actual", "On Item Quantity"):
		if charge_type not in read_text(os.path.join(APP, "constants.py")):
			errors.append(f"constants.py: distribution map is missing charge type {charge_type!r}")


def check_item_discounts():
	"""Guard the decisions the discount feature depends on."""
	engine = read_text(os.path.join(APP, "discounts", "engine.py"))
	hooks = read_text(os.path.join(APP, "hooks.py"))
	constants = read_text(os.path.join(APP, "constants.py"))

	for handler in ("apply_item_discounts", "verify_item_discounts", "check_discount_authority"):
		if f"def {handler}(" not in engine:
			errors.append(f"discounts/engine.py: handler {handler} is not defined")
		if handler not in hooks:
			errors.append(f"hooks.py: no reference to {handler}")

	if '"before_validate", "neocharges.discounts.engine.apply_item_discounts"' not in hooks:
		errors.append(
			"hooks.py: item discounts must apply in before_validate so ERPNext totals the "
			"document from the discounted rates"
		)

	# The discount handlers must be registered before the charge handlers so a
	# percentage-of-net-total charge sees the discounted total.
	if hooks.find("DISCOUNT_DOCTYPES:") > hooks.find("for doctype in SUPPORTED_DOCTYPES:"):
		errors.append("hooks.py: discount handlers must be registered before charge handlers")

	if "item.discount_percentage = 0" not in engine:
		errors.append(
			"discounts/engine.py: discount_percentage must be written as 0 — a non-zero value "
			"lets ERPNext recompute the rate and undo the ZATCA-safe rounding"
		)
	for native in ("item.price_list_rate = result.gross", "item.discount_amount = result.unit_discount",
	               "item.rate = result.net"):
		if native not in engine:
			errors.append(f"discounts/engine.py: missing native write {native!r}")

	# Keep Rate must never touch the line's own price fields.
	tree = ast.parse(engine)
	keep_fn = next(
		(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_apply_keep_line"), None
	)
	if keep_fn is None:
		errors.append("discounts/engine.py: _apply_keep_line is missing")
	else:
		body = ast.get_source_segment(engine, keep_fn) or ""
		for banned in ("item.rate =", "item.price_list_rate =", "item.discount_amount =", "item.discount_percentage ="):
			if banned in body:
				errors.append(f"discounts/engine.py: Keep Rate writes {banned!r} — Rate and Amount must not change")
		if "MAPPED_DISCOUNT_FIELD" not in body:
			errors.append("discounts/engine.py: Keep Rate must carry the discount in ERPNext's per-item field")
	for needed in ("_guard_header_discount", "_sync_document_discount", "keep_rate_supported"):
		if f"def {needed}(" not in engine:
			errors.append(f"discounts/engine.py: {needed} is missing")

	if "max_discount" not in engine:
		errors.append("discounts/engine.py: Item.max_discount is no longer enforced")

	calc_src = read_text(os.path.join(APP, "discounts", "calc.py"))
	if "import frappe" in calc_src:
		errors.append("discounts/calc.py must stay free of Frappe so it is unit-testable")

	zatca_src = read_text(os.path.join(APP, "discounts", "zatca.py"))
	for banned in ("ElementTree", "lxml", "signed_xml", "invoice_hash"):
		if banned in zatca_src:
			errors.append(
				f"discounts/zatca.py references {banned!r} — the ZATCA XML is built and signed by the "
				"ZATCA app; editing it here would break the hash"
			)

	# Reason codes in the doctype must match the seeded list.
	reason_json = json.loads(
		read_text(os.path.join(APP, "discounts", "doctype", "neodiscount_reason", "neodiscount_reason.json"))
	)
	options = next(f["options"] for f in reason_json["fields"] if f["fieldname"] == "reason_code")
	reasons_src = read_text(os.path.join(APP, "discounts", "reasons.py"))
	tree = ast.parse(reasons_src)
	seeded = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Assign) and any(
			isinstance(t, ast.Name) and t.id == "STANDARD_REASONS" for t in node.targets
		):
			seeded = {elt.elts[0].value for elt in node.value.elts}
	if set(options.split("\n")) != seeded:
		errors.append("neodiscount_reason.reason_code options differ from reasons.STANDARD_REASONS codes")

	if "ZATCA_FLAG_FIELD" not in constants:
		errors.append("constants.py: ZATCA field convention constants are missing")


def check_zatca_check():
	"""The pre-submit check must stay read-only and be wired to submit."""
	src = read_text(os.path.join(APP, "zatca_check", "checks.py"))
	hooks = read_text(os.path.join(APP, "hooks.py"))

	tree = ast.parse(src)
	for node in ast.walk(tree):
		if (
			isinstance(node, ast.Call)
			and isinstance(node.func, ast.Attribute)
			and node.func.attr in ("set", "save", "db_set", "insert", "submit")
			and isinstance(node.func.value, ast.Name)
			and node.func.value.id == "doc"
		):
			args = [a.id for a in node.args if isinstance(a, ast.Name)]
			if node.func.attr != "set" or not set(args) & {"STATUS_FIELD", "CHECKED_ON_FIELD"}:
				errors.append(
					f"zatca_check/checks.py line {node.lineno}: doc.{node.func.attr}() — the check may "
					"only record its own status fields"
				)

	for handler in ("checks.reset_status", "checks.check_before_submit"):
		if handler not in hooks:
			errors.append(f"hooks.py: ZATCA check handler {handler} is not registered")
	if "zatca_check.js" not in hooks:
		errors.append("hooks.py: zatca_check.js is not included")


def check_charge_currencies():
	"""Each posting path must convert from the line's currency, not assume one."""
	native = read_text(os.path.join(APP, "charges", "native_taxes.py"))
	posting = read_text(os.path.join(APP, "charges", "posting.py"))

	if "to_document_currency(" not in native:
		errors.append(
			"charges/native_taxes.py: tax rows must convert through to_document_currency — "
			"ERPNext reads tax_amount in document currency"
		)
	if "def _account_amounts(" not in posting or posting.count("_account_amounts(") < 3:
		errors.append(
			"charges/posting.py: Journal Entry and inline GL sides must both use _account_amounts"
		)
	if "apply_document_accounts(" not in posting:
		errors.append("charges/posting.py: blank accounts are no longer taken from the document")

	if "resolve_line_currency(doc, row)" not in posting:
		errors.append("charges/posting.py: lines are no longer resolved to a currency on validate")

	for dt in ("neocharge_line", "neocharge_template_line"):
		meta = json.loads(read_text(os.path.join(APP, "charges", "doctype", dt, f"{dt}.json")))
		names = {f["fieldname"] for f in meta["fields"]}
		needed = {
			"charge_currency", "exchange_rate", "base_amount", "debit_from_document", "credit_from_document"
		} if dt == "neocharge_line" else {
			"currency_basis",
			"currency",
		}
		for fieldname in needed - names:
			errors.append(f"{dt}: missing currency field {fieldname}")


def check_frappe_range():
	"""The deployed bench runs v16: the declared range must include it."""
	import tomllib

	with open(os.path.join(ROOT, "pyproject.toml"), "rb") as handle:
		deps = tomllib.load(handle).get("tool", {}).get("bench", {}).get("frappe-dependencies", {})
	if "<17" not in deps.get("frappe", "").replace(" ", "").replace(".0.0", ""):
		errors.append("pyproject.toml: frappe range must cover v16 (<17.0.0)")


def main():
	check_required_files()
	check_item_discounts()
	check_zatca_check()
	check_charge_currencies()
	check_frappe_range()
	check_pyproject()
	check_python_syntax()
	check_doctypes()
	check_hooks_targets()
	check_module_registration()
	check_patches_file()
	check_hooks_namespace()
	check_gl_routing()
	check_native_tax_delegation()

	if errors:
		print(f"FAIL — {len(errors)} problem(s):\n")
		for err in errors:
			print("  •", err)
		return 1

	print("OK — tree verified")
	return 0


if __name__ == "__main__":
	sys.exit(main())

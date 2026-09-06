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
	"neocharges/public/js/charges.js",
	"pyproject.toml",
	"README.md",
]

REQUIRED_DOCTYPES = [
	"neocharges_settings",
	"neocharge_doctype_config",
	"neocharge_template",
	"neocharge_template_line",
	"neocharge_line",
]

EXPECTED_MODULE = "Charges"

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
	base = os.path.join(APP, "charges", "doctype")
	for slug in REQUIRED_DOCTYPES:
		folder = os.path.join(base, slug)
		if not os.path.isdir(folder):
			errors.append(f"missing doctype folder: {slug}")
			continue

		for suffix in ("__init__.py", f"{slug}.json"):
			if not os.path.isfile(os.path.join(folder, suffix)):
				errors.append(f"{slug}: missing {suffix}")

		json_path = os.path.join(folder, f"{slug}.json")
		if not os.path.isfile(json_path):
			continue

		try:
			meta = json.loads(read_text(json_path))
		except json.JSONDecodeError as exc:
			errors.append(f"{slug}: invalid JSON — {exc}")
			continue

		if meta.get("module") != EXPECTED_MODULE:
			errors.append(f"{slug}: module is {meta.get('module')!r}, expected {EXPECTED_MODULE!r}")

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
	modules = read_text(os.path.join(APP, "modules.txt")).split()
	if EXPECTED_MODULE not in modules:
		errors.append(f"modules.txt does not declare {EXPECTED_MODULE!r}")



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


def main():
	check_required_files()
	check_pyproject()
	check_python_syntax()
	check_doctypes()
	check_hooks_targets()
	check_module_registration()
	check_patches_file()
	check_hooks_namespace()
	check_gl_routing()

	if errors:
		print(f"FAIL — {len(errors)} problem(s):\n")
		for err in errors:
			print("  •", err)
		return 1

	print("OK — tree verified")
	return 0


if __name__ == "__main__":
	sys.exit(main())

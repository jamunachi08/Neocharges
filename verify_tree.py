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


def main():
	check_required_files()
	check_pyproject()
	check_python_syntax()
	check_doctypes()
	check_hooks_targets()
	check_module_registration()

	if errors:
		print(f"FAIL — {len(errors)} problem(s):\n")
		for err in errors:
			print("  •", err)
		return 1

	print("OK — tree verified")
	return 0


if __name__ == "__main__":
	sys.exit(main())

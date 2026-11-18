from frappe.model.document import Document


class NeoChargeLine(Document):
	"""Charge line.

	Validation lives in ``neocharges.charges.posting`` so that it runs with
	the parent document's company and posting date in scope.
	"""

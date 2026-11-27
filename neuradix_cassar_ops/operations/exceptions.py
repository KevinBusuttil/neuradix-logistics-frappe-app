from __future__ import annotations

import frappe


class OperationsValidationError(frappe.ValidationError):
	pass


class OperationsPermissionError(frappe.PermissionError):
	pass


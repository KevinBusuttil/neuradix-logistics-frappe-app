from __future__ import annotations

import frappe
from frappe.model.document import Document


class CassarOperationsSettings(Document):
	def validate(self) -> None:
		max_stops = int(self.max_stops_per_plan or 0)
		if max_stops < 1 or max_stops > 500:
			frappe.throw(
				"Maximum Stops Per Plan must be between 1 and 500.",
				frappe.ValidationError,
			)

		if not self.default_source_warehouse:
			return

		warehouses = frappe.db.sql(
			"""
			SELECT company, is_group, disabled
			FROM `tabWarehouse`
			WHERE name = %(warehouse)s
			LIMIT 1
			""",
			{"warehouse": self.default_source_warehouse},
			as_dict=True,
		)
		if not warehouses:
			frappe.throw("Default Source Warehouse does not exist.", frappe.ValidationError)

		warehouse = warehouses[0]
		if warehouse.is_group or warehouse.disabled:
			frappe.throw(
				"Default Source Warehouse must be an enabled leaf warehouse.",
				frappe.ValidationError,
			)
		if self.default_company and warehouse.company != self.default_company:
			frappe.throw(
				"Default Source Warehouse must belong to Default Company.",
				frappe.ValidationError,
			)

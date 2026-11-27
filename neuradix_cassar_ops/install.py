from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


OPERATIONS_ROLES = (
	"Cassar Operations Manager",
	"Cassar Picker",
	"Cassar Checker",
	"Cassar Loader",
	"Cassar Driver",
)


def after_install() -> None:
	ensure_operations_roles()
	ensure_operations_custom_fields()


def after_migrate() -> None:
	ensure_operations_roles()
	ensure_operations_custom_fields()


def ensure_operations_roles() -> None:
	for role_name in OPERATIONS_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role_name,
				"desk_access": 1,
				"is_custom": 1,
			}
		).insert(ignore_permissions=True)


def ensure_operations_custom_fields() -> None:
	field_map = {
		"Pick List": [
			{
				"fieldname": "custom_checker",
				"label": "Checker",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "custom_picking_status",
			},
			{
				"fieldname": "custom_checking_status",
				"label": "Checking Status",
				"fieldtype": "Select",
				"options": "Pending\nIn Progress\nApproved\nRejected",
				"default": "Pending",
				"insert_after": "custom_checker",
			},
			{
				"fieldname": "custom_checked_at",
				"label": "Checked At",
				"fieldtype": "Datetime",
				"read_only": 1,
				"insert_after": "custom_checking_status",
			},
			{
				"fieldname": "custom_check_notes",
				"label": "Check Notes",
				"fieldtype": "Small Text",
				"insert_after": "custom_checked_at",
			},
		],
		"Vehicle": [
			{
				"fieldname": "custom_transit_warehouse",
				"label": "Transit Warehouse",
				"fieldtype": "Link",
				"options": "Warehouse",
				"insert_after": "company",
			},
		],
		"Sales Order": [
			{
				"fieldname": "custom_order_source",
				"label": "Order Source",
				"fieldtype": "Data",
				"insert_after": "custom_is_parked",
			},
			{
				"fieldname": "custom_ops_status",
				"label": "Operations Status",
				"fieldtype": "Select",
				"options": "Order Confirmed\nPlanned\nPicking\nPicked\nChecked\nLoading\nLoaded\nDispatched\nPartially Delivered\nDelivered\nDelivery Failed\nBackordered\nClosed",
				"default": "Order Confirmed",
				"insert_after": "custom_order_source",
			},
			{
				"fieldname": "custom_ops_hold_reason",
				"label": "Operations Hold Reason",
				"fieldtype": "Small Text",
				"insert_after": "custom_ops_status",
			},
			{
				"fieldname": "custom_transport_plan",
				"label": "Transport Plan",
				"fieldtype": "Link",
				"options": "Transport Plan",
				"insert_after": "custom_ops_hold_reason",
			},
			{
				"fieldname": "custom_transport_route",
				"label": "Transport Route",
				"fieldtype": "Link",
				"options": "Transport Route",
				"insert_after": "custom_transport_plan",
			},
		],
		"Stock Entry": _ops_link_fields("purpose"),
		"Sales Invoice": _ops_link_fields("customer"),
		"Delivery Note": _ops_link_fields("customer"),
		"Payment Entry": _ops_link_fields("party"),
	}
	filtered: dict[str, list[dict]] = {}
	for doctype, fields in field_map.items():
		meta = frappe.get_meta(doctype)
		missing = [field for field in fields if not meta.has_field(field["fieldname"])]
		if missing:
			filtered[doctype] = missing
	if filtered:
		create_custom_fields(filtered, update=True)


def _ops_link_fields(insert_after: str) -> list[dict]:
	return [
		{
			"fieldname": "custom_transport_route",
			"label": "Transport Route",
			"fieldtype": "Link",
			"options": "Transport Route",
			"insert_after": insert_after,
		},
		{
			"fieldname": "custom_route_stop",
			"label": "Route Stop",
			"fieldtype": "Data",
			"insert_after": "custom_transport_route",
		},
		{
			"fieldname": "custom_ops_event_id",
			"label": "Operations Event ID",
			"fieldtype": "Data",
			"read_only": 1,
			"insert_after": "custom_route_stop",
		},
	]

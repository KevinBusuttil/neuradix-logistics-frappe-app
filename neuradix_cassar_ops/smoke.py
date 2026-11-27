from __future__ import annotations

import frappe

from neuradix_cassar_ops.operations.insight_gateway import InsightTransportGateway
from neuradix_cassar_ops.operations.mobile_repository import SQLMobileOperationsRepository
from neuradix_cassar_ops.operations.mobile_services import (
	get_mobile_bootstrap,
	list_pick_queue,
	list_plans,
	list_route_queue,
)


def assert_operations_ready() -> dict:
	"""Verify the read-only mobile facade against the installed Insight stack."""
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		gateway = InsightTransportGateway()
		repository = SQLMobileOperationsRepository()
		roles = frappe.get_roles("Administrator")
		capabilities = gateway.capabilities()
		missing = [name for name, enabled in capabilities.items() if not enabled]
		if missing:
			frappe.throw(f"Missing Insight operations capabilities: {', '.join(missing)}")
		if not frappe.db.exists("DocType", "Cassar Fulfilment Event"):
			frappe.throw("Cassar Fulfilment Event is not installed.")

		bootstrap = get_mobile_bootstrap(user="Administrator", roles=roles, gateway=gateway)
		plans = list_plans(repository, roles=roles, plan_date=None, limit=5)
		available = list_pick_queue(
			repository,
			user="Administrator",
			roles=roles,
			queue="available",
			status=None,
			limit=5,
		)
		routes = list_route_queue(
			repository,
			user="Administrator",
			roles=roles,
			delivery_date=None,
			limit=5,
		)
		return {
			"status": "ready",
			"capabilities": capabilities,
			"features": bootstrap["features"],
			"sample_counts": {
				"plans": plans["count"],
				"available_pick_lists": available["count"],
				"routes": routes["count"],
			},
		}
	finally:
		frappe.set_user(previous_user)


__all__ = ["assert_operations_ready"]

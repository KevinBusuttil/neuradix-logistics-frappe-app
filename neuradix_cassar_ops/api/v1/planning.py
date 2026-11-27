from __future__ import annotations

import json

import frappe

from neuradix_cassar_ops.operations.insight_gateway import InsightTransportGateway
from neuradix_cassar_ops.operations.mobile_repository import SQLMobileOperationsRepository
from neuradix_cassar_ops.operations.mobile_services import (
	PLANNER_ROLES,
	_require_any,
	create_insight_plan,
	list_plans,
)


def _roles() -> list[str]:
	return frappe.get_roles(frappe.session.user)


@frappe.whitelist()
def get_plans(plan_date: str | None = None, limit: int = 100) -> dict:
	return list_plans(
		SQLMobileOperationsRepository(),
		roles=_roles(),
		plan_date=plan_date,
		limit=int(limit),
	)


@frappe.whitelist(methods=["POST"])
def create_plan(
	plan_date: str,
	company: str,
	source_warehouse: str,
	notes: str = "",
) -> dict:
	return create_insight_plan(
		InsightTransportGateway(),
		roles=_roles(),
		plan_date=plan_date,
		company=company,
		source_warehouse=source_warehouse,
		notes=notes,
	)


@frappe.whitelist(methods=["POST"])
def preview_routes(
	plan_name: str,
	vehicles_json: str | None = None,
	nl_instructions: str | None = None,
) -> dict:
	_require_any(_roles(), PLANNER_ROLES, "Transport planning access is required.")
	return InsightTransportGateway().preview_routes(
		plan_name=plan_name,
		vehicles_json=vehicles_json,
		nl_instructions=nl_instructions,
	)


@frappe.whitelist(methods=["POST"])
def apply_routes(plan_name: str, clusters_json: str, enhance: int = 0) -> dict:
	_require_any(_roles(), PLANNER_ROLES, "Transport planning access is required.")
	clusters = json.loads(clusters_json or "[]")
	if not isinstance(clusters, list):
		frappe.throw("clusters_json must be a JSON array.", frappe.ValidationError)
	return InsightTransportGateway().apply_routes(
		plan_name=plan_name,
		clusters=clusters,
		enhance=bool(int(enhance)),
	)


@frappe.whitelist(methods=["POST"])
def create_pick_lists(plan_name: str, routes_json: str | None = None) -> dict:
	_require_any(_roles(), PLANNER_ROLES, "Transport planning access is required.")
	routes = json.loads(routes_json) if routes_json else None
	if routes is not None and not isinstance(routes, list):
		frappe.throw("routes_json must be a JSON array.", frappe.ValidationError)
	created = InsightTransportGateway().create_pick_lists(plan_name=plan_name, routes=routes)
	return {
		"created": created,
		"created_count": sum(len(names) for names in created.values()),
	}

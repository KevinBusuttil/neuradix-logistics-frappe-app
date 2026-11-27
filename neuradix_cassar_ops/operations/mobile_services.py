from __future__ import annotations

from typing import Any, Iterable

from neuradix_cassar_ops.operations.exceptions import OperationsPermissionError


PLANNER_ROLES = frozenset({"System Manager", "Transport Manager", "Dispatcher"})
PICKING_ROLES = frozenset(
	{"System Manager", "Stock Manager", "Warehouse Manager", "Cassar Operations Manager", "Cassar Picker"}
)
CHECKING_ROLES = frozenset(
	{"System Manager", "Stock Manager", "Warehouse Manager", "Cassar Operations Manager", "Cassar Checker"}
)
ROUTE_ROLES = frozenset(
	{"System Manager", "Transport Manager", "Dispatcher", "Cassar Operations Manager", "Cassar Loader", "Cassar Driver"}
)
DELIVERY_ROLES = frozenset(
	{"System Manager", "Transport Manager", "Dispatcher", "Cassar Operations Manager", "Cassar Driver"}
)
ROUTE_MANAGER_ROLES = frozenset(
	{"System Manager", "Transport Manager", "Dispatcher", "Cassar Operations Manager", "Cassar Loader"}
)


def _require_any(roles: Iterable[str], allowed: frozenset[str], message: str) -> None:
	if set(roles).isdisjoint(allowed):
		raise OperationsPermissionError(message)


def get_mobile_bootstrap(*, user: str, roles: Iterable[str], gateway) -> dict[str, Any]:
	role_set = set(roles)
	return {
		"user": user,
		"roles": sorted(role_set),
		"features": {
				"planning": not role_set.isdisjoint(PLANNER_ROLES),
				"picking": not role_set.isdisjoint(PICKING_ROLES),
				"checking": not role_set.isdisjoint(CHECKING_ROLES),
				"loading": not role_set.isdisjoint(ROUTE_MANAGER_ROLES),
				"delivery": not role_set.isdisjoint(DELIVERY_ROLES),
				"offline_events": True,
			},
		"insight": gateway.capabilities(),
	}


def list_plans(repository, *, roles: Iterable[str], plan_date: str | None, limit: int) -> dict:
	_require_any(roles, PLANNER_ROLES, "Transport planning access is required.")
	rows = repository.list_transport_plans(plan_date=plan_date, limit=limit)
	return {"count": len(rows), "plans": rows}


def create_insight_plan(
	gateway,
	*,
	roles: Iterable[str],
	plan_date: str,
	company: str,
	source_warehouse: str,
	notes: str = "",
) -> dict:
	_require_any(roles, PLANNER_ROLES, "Transport planning access is required.")
	return {"plan": gateway.create_plan(
		plan_date=plan_date,
		company=company,
		source_warehouse=source_warehouse,
		notes=notes,
	)}


def list_pick_queue(repository, *, user: str, roles: Iterable[str], queue: str, status: str | None, limit: int):
	allowed = CHECKING_ROLES if queue == "checking" else PICKING_ROLES
	_require_any(roles, allowed, "Warehouse picking access is required.")
	rows = repository.list_pick_lists(user=user, queue=queue, status=status, limit=limit)
	return {"count": len(rows), "pick_lists": rows}


def get_pick_detail(repository, *, user: str, roles: Iterable[str], pick_list_name: str):
	_require_any(roles, PICKING_ROLES | CHECKING_ROLES, "Warehouse picking access is required.")
	detail = repository.get_pick_list(pick_list_name=pick_list_name)
	role_set = set(roles)
	if detail.get("picker") and detail["picker"] != user and role_set.isdisjoint(
		{"System Manager", "Stock Manager", "Warehouse Manager", "Cassar Operations Manager", "Cassar Checker"}
	):
		raise OperationsPermissionError("This Pick List is assigned to another picker.")
	return {"pick_list": detail}


def list_route_queue(
	repository,
	*,
	user: str,
	roles: Iterable[str],
	delivery_date: str | None,
	limit: int,
):
	_require_any(roles, ROUTE_ROLES, "Transport route access is required.")
	role_set = set(roles)
	rows = repository.list_routes(
		delivery_date=delivery_date,
		limit=limit,
		user=user,
		driver_only=role_set.isdisjoint(ROUTE_MANAGER_ROLES),
	)
	return {"count": len(rows), "routes": rows}


def get_route_detail(repository, *, user: str, roles: Iterable[str], route_name: str) -> dict:
	_require_any(roles, ROUTE_ROLES, "Transport route access is required.")
	detail = repository.get_route(route_name=route_name)
	if set(roles).isdisjoint(ROUTE_MANAGER_ROLES) and detail.get("driver_user") != user:
		raise OperationsPermissionError("This Transport Route is assigned to another driver.")
	return {"route": detail}

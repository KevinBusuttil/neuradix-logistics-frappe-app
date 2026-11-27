from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from typing import Any

import frappe
from frappe.utils import add_days, getdate, nowdate
from frappe.utils.password import update_password

from erpnext.selling.doctype.sales_order.sales_order import create_pick_list

from neuradix.pos.demo_seed import (
	DEMO_CUSTOMERS,
	DEMO_ITEMS,
	_suspend_insight_trade_agreement_validate,
	seed_demo_site,
)
from neuradix.pos.repositories import SQLNeuradixRepository
from neuradix_cassar_ops.install import ensure_operations_roles
from neuradix_cassar_ops.operations.insight_gateway import InsightTransportGateway
from neuradix_cassar_ops.operations.mobile_repository import SQLMobileOperationsRepository
from neuradix_cassar_ops.operations.mobile_services import (
	get_mobile_bootstrap,
	list_pick_queue,
	list_plans,
	list_route_queue,
)


DEMO_ORDER_PREFIX = "NEURADIX-OPS-DEMO-SO"
DEMO_PLAN_MARKER = "NEURADIX OPS DEMO PLAN"
DEMO_E2E_PLAN_MARKER = "NEURADIX OPS COMPLETE CYCLE DEMO"
DEMO_E2E_CLIENT_ORDER_ID = "NEURADIX-OPS-E2E-SO-001"
DEMO_E2E_POD_EVENT_ID = "neuradix-ops-e2e-pod-v1"
DEMO_E2E_PARTIAL_PLAN_MARKER = "NEURADIX OPS PARTIAL DELIVERY DEMO"
DEMO_E2E_PARTIAL_CLIENT_ORDER_ID = "NEURADIX-OPS-E2E-PARTIAL-SO-001"
DEMO_E2E_PARTIAL_POD_EVENT_ID = "neuradix-ops-e2e-partial-pod-v1"
DEMO_E2E_FAILED_PLAN_MARKER = "NEURADIX OPS FAILED DELIVERY DEMO"
DEMO_E2E_FAILED_CLIENT_ORDER_ID = "NEURADIX-OPS-E2E-FAILED-SO-001"
DEMO_E2E_FAILED_POD_EVENT_ID = "neuradix-ops-e2e-failed-pod-v1"
DEMO_INSIGHT_PLAN_MARKER = "NEURADIX OPS INSIGHT PLANNER DEMO"
DEMO_INSIGHT_CLIENT_ORDER_IDS = (
	"NEURADIX-OPS-INSIGHT-SO-001",
	"NEURADIX-OPS-INSIGHT-SO-002",
)
DEMO_MANUAL_PLANNING_PREFIX = "NEURADIX-OPS-PICK-UAT"


@dataclass(frozen=True)
class DemoOperationsUser:
	email: str
	full_name: str
	roles: tuple[str, ...]


@dataclass(frozen=True)
class DemoPlanSpec:
	code: str
	day_offset: int
	route_order_indexes: tuple[tuple[int, ...], ...]


DEMO_OPERATIONS_USERS = (
	DemoOperationsUser(
		"ops.manager@neuradix.local",
		"Olivia Operations",
		("Cassar Operations Manager", "Dispatcher", "Stock Manager", "System Manager"),
	),
	DemoOperationsUser(
		"transport.planner@neuradix.local",
		"Paul Transport Planner",
		("Dispatcher", "Transport Manager"),
	),
	DemoOperationsUser(
		"picker.one@neuradix.local",
		"Peter Picker",
		("Cassar Picker", "Stock User"),
	),
	DemoOperationsUser(
		"picker.two@neuradix.local",
		"Priya Picker",
		("Cassar Picker", "Stock User"),
	),
	DemoOperationsUser(
		"checker@neuradix.local",
		"Claire Checker",
		("Cassar Checker", "Stock User"),
	),
	DemoOperationsUser(
		"loader@neuradix.local",
		"Leo Loader",
		("Cassar Loader", "Stock User"),
	),
	DemoOperationsUser(
		"driver.one@neuradix.local",
		"Daniel Driver",
		("Cassar Driver",),
	),
	DemoOperationsUser(
		"driver.two@neuradix.local",
		"Daria Driver",
		("Cassar Driver",),
	),
)

DEMO_PLAN_SPECS = (
	DemoPlanSpec("TODAY", 0, ((0, 1), (2, 3))),
	DemoPlanSpec("TOMORROW", 1, ((4, 5),)),
	DemoPlanSpec("DAY-AFTER", 2, ((6, 7),)),
)

DEMO_ITEM_BARCODES = dict(
	zip(
		(item.item_code for item in DEMO_ITEMS),
		(
			"5902608260013",
			"5902608260020",
			"5902608260037",
			"5902608260044",
			"5902608260051",
			"5902608260068",
		),
		strict=True,
	)
)


def seed_demo_operations_data() -> dict[str, Any]:
	"""Create a repeatable local dataset for warehouse and transport UAT."""
	_assert_local_seed_allowed()
	password = _get_demo_operations_password()
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		base_seed = seed_demo_site()
		company = str(base_seed["company"])
		warehouse = str(base_seed["warehouse"])

		ensure_operations_roles()
		for user_spec in DEMO_OPERATIONS_USERS:
			_ensure_operations_user(user_spec, password)

		operations_warehouses = _ensure_operations_warehouses(company)
		_ensure_operations_settings(
			company=company,
			source_warehouse=warehouse,
			transit_warehouse=operations_warehouses["transit"],
			return_warehouse=operations_warehouses["returns"],
			damaged_warehouse=operations_warehouses["damaged"],
		)
		drivers = _ensure_drivers()
		vehicles = _ensure_vehicles(company, operations_warehouses["transit"])
		_ensure_item_barcodes()
		orders = _ensure_operations_orders(company)
		plans, routes_by_plan = _ensure_transport_plans(
			company=company,
			warehouse=warehouse,
			orders=orders,
			drivers=drivers,
			vehicles=vehicles,
		)
		pick_lists = _ensure_pick_lists(routes_by_plan)

		frappe.db.commit()
		return {
			"site": frappe.local.site,
			"company": company,
			"warehouse": warehouse,
			"operations_warehouses": operations_warehouses,
			"users": [user.email for user in DEMO_OPERATIONS_USERS],
			"sales_orders": orders,
			"transport_plans": plans,
			"transport_routes": [
				route_name
				for plan_routes in routes_by_plan.values()
				for route_name in plan_routes
			],
			"pick_lists": pick_lists,
			"barcodes": DEMO_ITEM_BARCODES,
			"counts": _count_seeded_records(),
		}
	finally:
		frappe.set_user(previous_user)


def assert_demo_operations_ready() -> dict[str, Any]:
	"""Seed twice, prove idempotency, and verify every role-specific queue."""
	seed_demo_operations_data()
	seeded = seed_demo_operations_data()
	counts = seeded["counts"]
	expected_counts = {
		"users": len(DEMO_OPERATIONS_USERS),
		"sales_orders": 8,
		"transport_plans": len(DEMO_PLAN_SPECS),
		"transport_routes": sum(len(spec.route_order_indexes) for spec in DEMO_PLAN_SPECS),
		"pick_lists": 8,
		"barcodes": len(DEMO_ITEM_BARCODES),
	}
	for key, expected in expected_counts.items():
		if counts[key] != expected:
			raise AssertionError(f"Expected {expected} seeded {key}, found {counts[key]}.")

	repository = SQLMobileOperationsRepository()
	gateway = InsightTransportGateway()
	manager = DEMO_OPERATIONS_USERS[0]
	picker_one = DEMO_OPERATIONS_USERS[2]
	checker = DEMO_OPERATIONS_USERS[4]
	driver_one = DEMO_OPERATIONS_USERS[6]

	manager_roles = frappe.get_roles(manager.email)
	features = get_mobile_bootstrap(
		user=manager.email,
		roles=manager_roles,
		gateway=gateway,
	)["features"]
	if not all(features[name] for name in ("planning", "picking", "checking", "loading", "delivery")):
		raise AssertionError("The demo operations manager must expose every operations feature.")

	plans = list_plans(repository, roles=manager_roles, plan_date=None, limit=20)
	available = list_pick_queue(
		repository,
		user=picker_one.email,
		roles=frappe.get_roles(picker_one.email),
		queue="available",
		status=None,
		limit=20,
	)
	mine = list_pick_queue(
		repository,
		user=picker_one.email,
		roles=frappe.get_roles(picker_one.email),
		queue="mine",
		status=None,
		limit=20,
	)
	checking = list_pick_queue(
		repository,
		user=checker.email,
		roles=frappe.get_roles(checker.email),
		queue="checking",
		status=None,
		limit=20,
	)
	submitted_demo_picks = _sql_count(
		"""
		SELECT COUNT(*) AS total
		FROM `tabPick List`
		WHERE docstatus = 1 AND custom_transport_plan IN %(plans)s
		""",
		{"plans": tuple(seeded["transport_plans"])},
	)
	routes = list_route_queue(
		repository,
		user=driver_one.email,
		roles=frappe.get_roles(driver_one.email),
		delivery_date=None,
		limit=20,
	)

	if len(plans["plans"]) < len(DEMO_PLAN_SPECS):
		raise AssertionError("The planner queue does not contain all seeded transport plans.")
	if not available["pick_lists"]:
		raise AssertionError("The available picking queue is empty after seeding.")
	if not mine["pick_lists"]:
		raise AssertionError("Picker One has no assigned Pick List after seeding.")
	if len(checking["pick_lists"]) + submitted_demo_picks < 2:
		raise AssertionError("The checker queue must contain at least two completed picks.")
	if not routes["routes"]:
		raise AssertionError("Driver One has no assigned route after seeding.")

	return {
		**seeded,
		"queue_counts": {
			"plans": len(plans["plans"]),
			"available": len(available["pick_lists"]),
			"picker_one_mine": len(mine["pick_lists"]),
			"checking": len(checking["pick_lists"]),
			"submitted_picks": submitted_demo_picks,
			"driver_one_routes": len(routes["routes"]),
		},
	}


def assert_demo_fulfilment_cycle() -> dict[str, Any]:
	"""Execute and verify a real ERPNext-backed happy path exactly once."""
	_assert_local_seed_allowed()
	seeded = seed_demo_operations_data()
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		existing_event = frappe.db.get_value(
			"Cassar Delivery Event",
			DEMO_E2E_POD_EVENT_ID,
			["transport_route", "delivery_note", "sales_invoice", "payment_entry"],
			as_dict=True,
		)
		if existing_event:
			return _assert_existing_fulfilment_cycle(existing_event)

		order_name = _ensure_e2e_sales_order(str(seeded["company"]))
		plan, route, pick_list = _ensure_e2e_transport_documents(
			company=str(seeded["company"]),
			warehouse=str(seeded["warehouse"]),
			order_name=order_name,
		)
		_run_e2e_pick(pick_list)
		_run_e2e_check(pick_list)
		dispatch_result = _run_e2e_load_and_dispatch(route)
		delivery_result = _run_e2e_delivery(route)
		frappe.db.commit()
		return {
			"status": "complete",
			"sales_order": order_name,
			"transport_plan": plan,
			"transport_route": route,
			"pick_list": pick_list,
			"stock_entry": dispatch_result["result"]["stock_entry"],
			"sales_invoice": delivery_result["result"]["sales_invoices"][0],
			"delivery_note": delivery_result["result"]["delivery_notes"][0],
			"payment_entry": delivery_result["result"]["payment_entries"][0],
			"delivery_event": delivery_result["result"]["delivery_event"],
			"route_status": delivery_result["result"]["route_status"],
		}
	finally:
		frappe.set_user(previous_user)


def assert_demo_delivery_exception_cycles() -> dict[str, Any]:
	"""Execute real partial and failed deliveries and verify ERP reconciliation."""
	_assert_local_seed_allowed()
	seeded = seed_demo_operations_data()
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		partial = _run_or_assert_exception_cycle(
			company=str(seeded["company"]),
			warehouse=str(seeded["warehouse"]),
			client_order_id=DEMO_E2E_PARTIAL_CLIENT_ORDER_ID,
			plan_marker=DEMO_E2E_PARTIAL_PLAN_MARKER,
			event_prefix="neuradix-ops-e2e-partial",
			pod_event_id=DEMO_E2E_PARTIAL_POD_EVENT_ID,
			delivery_result="Partially Delivered",
		)
		failed = _run_or_assert_exception_cycle(
			company=str(seeded["company"]),
			warehouse=str(seeded["warehouse"]),
			client_order_id=DEMO_E2E_FAILED_CLIENT_ORDER_ID,
			plan_marker=DEMO_E2E_FAILED_PLAN_MARKER,
			event_prefix="neuradix-ops-e2e-failed",
			pod_event_id=DEMO_E2E_FAILED_POD_EVENT_ID,
			delivery_result="Failed",
		)
		frappe.db.commit()
		return {"status": "complete", "partial": partial, "failed": failed}
	finally:
		frappe.set_user(previous_user)


def assert_demo_insight_planning_cycle() -> dict[str, Any]:
	"""Prove the live Insight preview/apply/Pick List handoff on the UAT site."""
	_assert_local_seed_allowed()
	seeded = seed_demo_operations_data()
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		delivery_date = add_days(nowdate(), 14)
		orders: list[str] = []
		for index, client_order_id in enumerate(DEMO_INSIGHT_CLIENT_ORDER_IDS):
			order_name = _ensure_e2e_sales_order(
				str(seeded["company"]),
				client_order_id=client_order_id,
				note="Neuradix Insight planner handoff smoke order",
				delivery_date=delivery_date,
				customer_index=index,
			)
			customer = frappe.db.get_value("Sales Order", order_name, "customer")
			address = _ensure_geocoded_customer_address(str(customer), index)
			frappe.db.set_value(
				"Sales Order",
				order_name,
				"shipping_address_name",
				address,
				update_modified=False,
			)
			orders.append(order_name)

		gateway = InsightTransportGateway()
		plan_name = frappe.db.get_value(
			"Transport Plan",
			{"notes": DEMO_INSIGHT_PLAN_MARKER},
			"name",
		)
		if not plan_name:
			plan_name = gateway.create_plan(
				plan_date=delivery_date,
				company=str(seeded["company"]),
				source_warehouse=str(seeded["warehouse"]),
				notes=DEMO_INSIGHT_PLAN_MARKER,
			)["name"]

		preview = gateway.preview_routes(plan_name=str(plan_name))
		clusters = list(preview.get("clusters") or [])
		if int(preview.get("total_stops") or 0) < len(orders) or not clusters:
			raise AssertionError(
				f"Insight did not propose routes for both UAT orders: {preview}."
			)
		routes = _plan_route_names(str(plan_name))
		if not routes:
			apply_result = gateway.apply_routes(
				plan_name=str(plan_name),
				clusters=clusters,
				enhance=False,
			)
			routes = [str(name) for name in apply_result.get("created_routes") or []]
		else:
			gateway._ensure_route_source_links(created_routes=routes, clusters=clusters)
		if not routes:
			raise AssertionError("Insight did not create any Transport Routes.")

		pick_lists_by_route = gateway.create_pick_lists(
			plan_name=str(plan_name),
			routes=routes,
		)
		pick_lists = [
			str(name)
			for route_pick_lists in pick_lists_by_route.values()
			for name in route_pick_lists
		]
		linked_orders = {
			str(row.ref_name)
			for row in frappe.db.sql(
				"""
				SELECT ref_name
				FROM `tabRoute Stop Document`
				WHERE parent IN %(routes)s
					AND parenttype = 'Transport Route'
					AND parentfield = 'stop_documents'
					AND ref_type = 'Sales Order'
				""",
				{"routes": tuple(routes)},
				as_dict=True,
			)
		}
		if not set(orders).issubset(linked_orders):
			raise AssertionError("Insight routes are missing one or more source Sales Orders.")
		if len(pick_lists) < len(orders):
			raise AssertionError("Insight did not create a Pick List for every UAT Sales Order.")
		frappe.db.commit()
		return {
			"status": "ready",
			"transport_plan": plan_name,
			"sales_orders": orders,
			"transport_routes": routes,
			"pick_lists": pick_lists,
			"preview_stop_count": int(preview.get("total_stops") or 0),
			"preview_warnings": list(preview.get("warnings") or []),
		}
	finally:
		frappe.set_user(previous_user)


def create_manual_planning_order_batch(
	count: int = 10,
	delivery_date: str | None = None,
	batch_code: str = "A",
) -> dict[str, Any]:
	"""Create an idempotent set of submitted orders for manual route/picking UAT."""
	_assert_local_seed_allowed()
	order_ids = _planning_batch_client_order_ids(count=count, batch_code=batch_code)
	seeded = seed_demo_operations_data()
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		target_date = _resolve_planning_batch_date(
			client_order_ids=order_ids,
			delivery_date=delivery_date,
		)
		orders: list[str] = []
		for index, client_order_id in enumerate(order_ids):
			order_name = _ensure_e2e_sales_order(
				str(seeded["company"]),
				client_order_id=client_order_id,
				note=f"Manual operations planning UAT {client_order_id}",
				delivery_date=target_date,
				customer_index=index % len(DEMO_CUSTOMERS),
			)
			customer = str(frappe.db.get_value("Sales Order", order_name, "customer"))
			address = _ensure_geocoded_customer_address(
				customer,
				index % len(DEMO_CUSTOMERS),
			)
			frappe.db.set_value(
				"Sales Order",
				order_name,
				{
					"delivery_date": target_date,
					"shipping_address_name": address,
					"custom_order_source": "Neuradix POS",
					"custom_ops_status": "Order Confirmed",
				},
				update_modified=False,
			)
			orders.append(order_name)

		linked_routes = _linked_routes_for_orders(orders)
		eligible_orders = _eligible_planning_orders(orders, target_date)
		if len(eligible_orders) != len(orders):
			raise AssertionError(
				"One or more generated Sales Orders do not satisfy Insight's planning filters."
			)
		all_candidates = _count_insight_candidates(target_date, str(seeded["company"]))
		frappe.db.commit()
		return {
			"status": "already_planned" if linked_routes else "ready",
			"batch_code": batch_code.upper(),
			"delivery_date": target_date,
			"company": seeded["company"],
			"source_warehouse": seeded["warehouse"],
			"sales_orders": orders,
			"eligible_order_count": len(eligible_orders),
			"all_candidate_orders_on_date": all_candidates,
			"linked_routes": linked_routes,
		}
	finally:
		frappe.set_user(previous_user)


def _planning_batch_client_order_ids(*, count: int, batch_code: str) -> tuple[str, ...]:
	count = int(count)
	batch_code = str(batch_code or "").strip().upper()
	if count < 1 or count > 100:
		raise ValueError("Planning UAT batch count must be between 1 and 100.")
	if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,31}", batch_code):
		raise ValueError(
			"Planning UAT batch code must contain only letters, numbers, hyphens, or underscores."
		)
	return tuple(
		f"{DEMO_MANUAL_PLANNING_PREFIX}-{batch_code}-{index:03d}"
		for index in range(1, count + 1)
	)


def _resolve_planning_batch_date(
	*,
	client_order_ids: tuple[str, ...],
	delivery_date: str | None,
) -> str:
	existing_dates = {
		str(row.delivery_date)
		for row in frappe.db.sql(
			"""
			SELECT DISTINCT sales_order.delivery_date
			FROM `tabNeuradix POS Sync Log` sync_log
			INNER JOIN `tabSales Order` sales_order
				ON sales_order.name = sync_log.sales_order
			WHERE sync_log.client_order_id IN %(client_order_ids)s
				AND sales_order.docstatus = 1
			""",
			{"client_order_ids": client_order_ids},
			as_dict=True,
		)
	}
	if len(existing_dates) > 1:
		raise RuntimeError("The existing planning UAT batch spans multiple delivery dates.")
	if existing_dates:
		return existing_dates.pop()
	if delivery_date:
		return str(getdate(delivery_date))

	start_date = getdate(add_days(nowdate(), 21))
	end_date = getdate(add_days(start_date, 69))
	occupied_dates = {
		getdate(row.delivery_date)
		for row in frappe.db.sql(
			"""
			SELECT DISTINCT delivery_date
			FROM `tabSales Order`
			WHERE docstatus = 1
				AND (COALESCE(per_delivered, 0) < 100 OR COALESCE(per_billed, 0) < 100)
				AND delivery_date BETWEEN %(start_date)s AND %(end_date)s
			""",
			{"start_date": start_date, "end_date": end_date},
			as_dict=True,
		)
	}
	for offset in range(70):
		candidate = getdate(add_days(start_date, offset))
		if candidate not in occupied_dates:
			return str(candidate)
	return str(getdate(add_days(end_date, 1)))


def _eligible_planning_orders(order_names: list[str], delivery_date: str) -> list[str]:
	return [
		str(row.name)
		for row in frappe.db.sql(
			"""
			SELECT sales_order.name
			FROM `tabSales Order` sales_order
			INNER JOIN `tabAddress` address
				ON address.name = sales_order.shipping_address_name
			WHERE sales_order.name IN %(order_names)s
				AND sales_order.docstatus = 1
				AND (
					COALESCE(sales_order.per_delivered, 0) < 100
					OR COALESCE(sales_order.per_billed, 0) < 100
				)
				AND sales_order.delivery_date = %(delivery_date)s
				AND address.custom_latitude IS NOT NULL
				AND address.custom_longitude IS NOT NULL
			ORDER BY sales_order.name
			""",
			{"order_names": tuple(order_names), "delivery_date": delivery_date},
			as_dict=True,
		)
	]


def _linked_routes_for_orders(order_names: list[str]) -> dict[str, str]:
	return {
		str(row.ref_name): str(row.parent)
		for row in frappe.db.sql(
			"""
			SELECT ref_name, parent
			FROM `tabRoute Stop Document`
			WHERE parenttype = 'Transport Route'
				AND parentfield = 'stop_documents'
				AND ref_type = 'Sales Order'
				AND ref_name IN %(order_names)s
			ORDER BY creation DESC
			""",
			{"order_names": tuple(order_names)},
			as_dict=True,
		)
	}


def _count_insight_candidates(delivery_date: str, company: str) -> int:
	rows = frappe.db.sql(
		"""
		SELECT COUNT(*) AS total
		FROM `tabSales Order`
		WHERE docstatus = 1
			AND (COALESCE(per_delivered, 0) < 100 OR COALESCE(per_billed, 0) < 100)
			AND delivery_date = %(delivery_date)s
			AND company = %(company)s
		""",
		{"delivery_date": delivery_date, "company": company},
		as_dict=True,
	)
	return int(rows[0].total or 0)


def _plan_route_names(plan_name: str) -> list[str]:
	return [
		str(row.route)
		for row in frappe.db.sql(
			"""
			SELECT route
			FROM `tabTransport Plan Route`
			WHERE parent = %(plan_name)s
				AND parenttype = 'Transport Plan'
				AND parentfield = 'routes'
				AND COALESCE(route, '') != ''
			ORDER BY idx ASC
			""",
			{"plan_name": plan_name},
			as_dict=True,
		)
	]


def _ensure_geocoded_customer_address(customer: str, index: int) -> str:
	city = frappe.db.get_value("City", {"name": "Valletta"}, "name")
	if not city:
		city = frappe.get_doc({"doctype": "City", "city": "Valletta"}).insert(
			ignore_permissions=True
		).name
	rows = frappe.db.sql(
		"""
		SELECT link.parent
		FROM `tabDynamic Link` link
		WHERE link.parenttype = 'Address'
			AND link.parentfield = 'links'
			AND link.link_doctype = 'Customer'
			AND link.link_name = %(customer)s
		ORDER BY link.creation ASC
		LIMIT 1
		""",
		{"customer": customer},
		as_dict=True,
	)
	if rows:
		address = frappe.get_doc("Address", rows[0].parent)
	else:
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": customer,
				"address_type": "Shipping",
				"address_line1": f"{index + 1} Neuradix UAT Street",
				"city": city,
				"custom_city": city,
				"country": "Malta",
				"links": [{"link_doctype": "Customer", "link_name": customer}],
			}
		).insert(ignore_permissions=True)
	address.city = city
	address.custom_city = city
	address.custom_latitude = 35.898 + (index * 0.012)
	address.custom_longitude = 14.514 + (index * 0.014)
	address.save(ignore_permissions=True)
	return str(address.name)


def _run_or_assert_exception_cycle(
	*,
	company: str,
	warehouse: str,
	client_order_id: str,
	plan_marker: str,
	event_prefix: str,
	pod_event_id: str,
	delivery_result: str,
) -> dict[str, Any]:
	frappe.set_user("Administrator")
	existing = frappe.db.get_value(
		"Cassar Delivery Event",
		pod_event_id,
		[
			"transport_route",
			"delivery_result",
			"delivery_note",
			"sales_invoice",
			"return_stock_entry",
			"credit_note",
		],
		as_dict=True,
	)
	if existing:
		return _assert_existing_exception_cycle(existing, pod_event_id, delivery_result)

	order_name = _ensure_e2e_sales_order(
		company,
		client_order_id=client_order_id,
		note=f"Neuradix {delivery_result.lower()} delivery smoke cycle",
	)
	plan, route, pick_list = _ensure_e2e_transport_documents(
		company=company,
		warehouse=warehouse,
		order_name=order_name,
		plan_marker=plan_marker,
		instructions=f"{delivery_result} smoke delivery",
	)
	_run_e2e_pick(pick_list, event_prefix=event_prefix)
	_run_e2e_check(pick_list, event_prefix=event_prefix)
	_run_e2e_load_and_dispatch(route, event_prefix=event_prefix)

	delivered_quantities: list[dict[str, Any]] = []
	if delivery_result == "Partially Delivered":
		order_item = frappe.db.get_value(
			"Sales Order Item",
			{"parent": order_name},
			["item_code", "qty"],
			as_dict=True,
		)
		if not order_item or float(order_item.qty or 0) <= 1:
			raise AssertionError("Partial delivery fixture requires an order line above one unit.")
		delivered_quantities = [
			{
				"sales_order": order_name,
				"item_code": str(order_item.item_code),
				"qty": float(order_item.qty) - 1,
			}
		]

	result = _run_e2e_delivery(
		route,
		event_id=pod_event_id,
		delivery_result=delivery_result,
		delivered_quantities=delivered_quantities,
		failure_reason="Customer unavailable" if delivery_result == "Failed" else "",
		collection_amount=0,
	)
	event = frappe.db.get_value(
		"Cassar Delivery Event",
		pod_event_id,
		[
			"transport_route",
			"delivery_result",
			"delivery_note",
			"sales_invoice",
			"return_stock_entry",
			"credit_note",
		],
		as_dict=True,
	)
	if not event:
		raise AssertionError(f"Delivery event {pod_event_id} was not persisted.")
	verified = _assert_existing_exception_cycle(event, pod_event_id, delivery_result)
	return {
		**verified,
		"sales_order": order_name,
		"transport_plan": plan,
		"pick_list": pick_list,
		"api_status": result["status"],
	}


def _assert_existing_exception_cycle(
	event,
	event_id: str,
	delivery_result: str,
) -> dict[str, Any]:
	if event.delivery_result != delivery_result:
		raise AssertionError(
			f"Expected {delivery_result} for {event_id}, found {event.delivery_result}."
		)
	if frappe.db.get_value("Transport Route", event.transport_route, "status") != "Closed":
		raise AssertionError(f"Exception route {event.transport_route} was not closed.")
	for doctype, name in (
		("Sales Invoice", event.sales_invoice),
		("Stock Entry", event.return_stock_entry),
		("Sales Invoice", event.credit_note),
	):
		if not name or not frappe.db.exists(doctype, {"name": name, "docstatus": 1}):
			raise AssertionError(f"Missing submitted {doctype} for {delivery_result} cycle.")
	if delivery_result == "Partially Delivered":
		if not event.delivery_note or not frappe.db.exists(
			"Delivery Note",
			{"name": event.delivery_note, "docstatus": 1},
		):
			raise AssertionError("Partial delivery must retain a submitted Delivery Note.")
	elif event.delivery_note:
		raise AssertionError("Failed delivery must not submit a Delivery Note.")
	return {
		"delivery_result": delivery_result,
		"transport_route": event.transport_route,
		"delivery_event": event_id,
		"delivery_note": event.delivery_note or "",
		"sales_invoice": event.sales_invoice,
		"return_stock_entry": event.return_stock_entry,
		"credit_note": event.credit_note,
		"route_status": "Closed",
	}


def _ensure_e2e_sales_order(
	company: str,
	*,
	client_order_id: str = DEMO_E2E_CLIENT_ORDER_ID,
	note: str = "Neuradix complete POS-to-delivery smoke cycle",
	delivery_date: str | None = None,
	customer_index: int = 0,
) -> str:
	repository = SQLNeuradixRepository()
	existing = repository.find_sync_record(client_order_id)
	if existing and existing.sales_order and frappe.db.exists(
		"Sales Order",
		{"name": existing.sales_order, "docstatus": 1},
	):
		return str(existing.sales_order)
	if existing:
		frappe.delete_doc(
			"Neuradix POS Sync Log",
			existing.name,
			ignore_permissions=True,
			force=True,
		)
	customer = frappe.db.get_value(
		"Customer",
		{"custom_customer_id": DEMO_CUSTOMERS[customer_index].custom_customer_id},
		"name",
	)
	items = [item for item in DEMO_ITEMS if item.uom == "Unit"][:2]
	if not customer or len(items) < 2:
		raise RuntimeError("The base Neuradix demo data is incomplete.")
	taxes_template = frappe.db.get_single_value("Neuradix POS Settings", "sales_taxes_template")
	taxes = repository.get_sales_taxes(taxes_template) if taxes_template else []
	sync_record = repository.create_sync_record(
		client_order_id=client_order_id,
		customer=customer,
		request_hash=f"{client_order_id.lower()}-v1",
		client_platform="operations-e2e-smoke",
		payload_json="{}",
	)
	with _suspend_insight_trade_agreement_validate("sales_order"):
		order_name = repository.create_submitted_sales_order(
			{
				"client_order_id": client_order_id,
				"customer": customer,
				"hub_manager": "Administrator",
				"transaction_date": nowdate(),
				"delivery_date": delivery_date or nowdate(),
				"mode_of_payment": "Cash",
				"notes": note,
				"items": [
					{
						"item_code": item.item_code,
						"item_name": item.item_name,
						"qty": 2,
						"uom": item.uom,
						"rate": item.base_price,
						"notes": "Complete fulfilment smoke line",
					}
					for item in items
				],
				"order_taxes": taxes,
			}
		)
	repository.mark_sync_record_submitted(sync_record.name, order_name)
	frappe.db.set_value(
		"Sales Order",
		order_name,
		{"custom_order_source": "Neuradix POS", "custom_ops_status": "Order Confirmed"},
	)
	return str(order_name)


def _ensure_e2e_transport_documents(
	*,
	company: str,
	warehouse: str,
	order_name: str,
	plan_marker: str = DEMO_E2E_PLAN_MARKER,
	instructions: str = "Complete-cycle smoke delivery",
) -> tuple[str, str, str]:
	plan_name = frappe.db.get_value("Transport Plan", {"notes": plan_marker}, "name")
	if plan_name:
		plan = frappe.get_doc("Transport Plan", plan_name)
	else:
		plan = frappe.get_doc(
			{
				"doctype": "Transport Plan",
				"company": company,
				"plan_date": nowdate(),
				"source_warehouse": warehouse,
				"status": "Planned",
				"notes": plan_marker,
			}
		).insert(ignore_permissions=True)

	route_name = frappe.db.get_value(
		"Transport Route",
		{"transport_plan": plan.name, "docstatus": ["<", 2]},
		"name",
	)
	if route_name:
		route = frappe.get_doc("Transport Route", route_name)
	else:
		driver = frappe.db.get_value(
			"Driver",
			{"user": "driver.one@neuradix.local"},
			"name",
		)
		vehicle = frappe.db.get_value("Vehicle", {"license_plate": "OPS-DEMO-01"}, "name")
		order = frappe.db.get_value(
			"Sales Order",
			order_name,
			["customer", "grand_total"],
			as_dict=True,
		)
		route = frappe.get_doc(
			{
				"doctype": "Transport Route",
				"delivery_date": nowdate(),
				"transport_plan": plan.name,
				"status": "Created",
				"driver": driver,
				"vehicle": vehicle,
				"customers": 1,
				"sales_orders": 1,
			}
		)
		route.append(
			"stops",
			{
				"sequence": 1,
				"customer": order.customer,
				"status": "Queued",
				"special_instructions": instructions,
				"service_time_min": 10,
				"doc_count": 1,
				"planned_amount_due": order.grand_total,
			},
		)
		route.insert(ignore_permissions=True)
		route.append(
			"stop_documents",
			{
				"stop": route.stops[0].name,
				"ref_type": "Sales Order",
				"ref_name": order_name,
				"customer": order.customer,
				"amount_due": order.grand_total,
				"outstanding_amount": order.grand_total,
				"delivery_status": "Undelivered",
				"payment_status": "Unpaid",
			},
		)
		route.save(ignore_permissions=True)

	plan.reload()
	if not any(row.route == route.name for row in plan.routes):
		plan.append(
			"routes",
			{
				"route": route.name,
				"driver": route.driver,
				"vehicle": route.vehicle,
				"status": route.status,
				"stops_count": 1,
				"delivered_stops": 0,
			},
		)
	plan.status = "Planned"
	plan.total_routes = 1
	plan.total_stops = 1
	plan.save(ignore_permissions=True)
	frappe.db.set_value(
		"Sales Order",
		order_name,
		{
			"custom_ops_status": "Planned",
			"custom_transport_plan": plan.name,
			"custom_transport_route": route.name,
		},
	)

	pick_list_name = frappe.db.get_value(
		"Pick List Item",
		{"sales_order": order_name, "docstatus": ["<", 2]},
		"parent",
	)
	if pick_list_name:
		pick_list = frappe.get_doc("Pick List", pick_list_name)
	else:
		pick_list = create_pick_list(order_name)
		pick_list.custom_transport_plan = plan.name
		pick_list.custom_transport_route = route.name
		pick_list.insert(ignore_permissions=True)
	return str(plan.name), str(route.name), str(pick_list.name)


def _run_e2e_pick(
	pick_list_name: str,
	*,
	event_prefix: str = "neuradix-ops-e2e",
) -> None:
	from neuradix_cassar_ops.operations.event_service import (
		FrappeFulfilmentEventStore,
		process_pick_scan,
	)
	from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway
	from neuradix_cassar_ops.operations.workflow_service import finalize_pick

	repository = SQLMobileOperationsRepository()
	insight_gateway = InsightTransportGateway()
	fulfilment_gateway = ERPNextFulfilmentGateway(insight_gateway)
	event_store = FrappeFulfilmentEventStore()
	frappe.set_user("picker.one@neuradix.local")
	insight_gateway.claim_pick_list(pick_list_name)
	pick_list = frappe.get_doc("Pick List", pick_list_name)
	for index, row in enumerate(pick_list.locations, start=1):
		process_pick_scan(
			event={
			"event_id": f"{event_prefix}-pick-{index}-v1",
				"pick_list_name": pick_list.name,
				"scanned_code": row.item_code,
				"qty": float(row.qty),
				"device_id": "e2e-picker",
			},
			user=frappe.session.user,
			roles=frappe.get_roles(frappe.session.user),
			repository=repository,
			gateway=insight_gateway,
			event_store=event_store,
		)
	finalize_pick(
		event={
			"event_id": f"{event_prefix}-finalize-v1",
			"pick_list_name": pick_list.name,
			"device_id": "e2e-picker",
		},
		user=frappe.session.user,
		roles=frappe.get_roles(frappe.session.user),
		repository=repository,
		fulfilment_gateway=fulfilment_gateway,
		event_store=event_store,
	)


def _run_e2e_check(
	pick_list_name: str,
	*,
	event_prefix: str = "neuradix-ops-e2e",
) -> None:
	from neuradix_cassar_ops.operations.event_service import FrappeFulfilmentEventStore
	from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway
	from neuradix_cassar_ops.operations.workflow_service import approve_check, claim_check

	frappe.set_user("checker@neuradix.local")
	context = {
		"user": frappe.session.user,
		"roles": frappe.get_roles(frappe.session.user),
		"fulfilment_gateway": ERPNextFulfilmentGateway(),
		"event_store": FrappeFulfilmentEventStore(),
	}
	claim_check(
		event={
			"event_id": f"{event_prefix}-check-claim-v1",
			"pick_list_name": pick_list_name,
			"device_id": "e2e-checker",
		},
		**context,
	)
	approve_check(
		event={
			"event_id": f"{event_prefix}-check-approve-v1",
			"pick_list_name": pick_list_name,
			"notes": "Independent quantity and item check passed.",
			"device_id": "e2e-checker",
		},
		**context,
	)


def _run_e2e_load_and_dispatch(
	route_name: str,
	*,
	event_prefix: str = "neuradix-ops-e2e",
) -> dict[str, Any]:
	from neuradix_cassar_ops.operations.event_service import FrappeFulfilmentEventStore
	from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway
	from neuradix_cassar_ops.operations.workflow_service import (
		begin_loading,
		complete_loading,
		dispatch_route,
		prepare_load_manifest,
		record_load_scan,
	)

	frappe.set_user("loader@neuradix.local")
	user = frappe.session.user
	roles = frappe.get_roles(user)
	repository = SQLMobileOperationsRepository()
	fulfilment_gateway = ERPNextFulfilmentGateway()
	event_store = FrappeFulfilmentEventStore()
	prepare_load_manifest(
		route_name=route_name,
		user=user,
		roles=roles,
		repository=repository,
		fulfilment_gateway=fulfilment_gateway,
	)
	begin_loading(
		event={
			"event_id": f"{event_prefix}-load-start-v1",
			"route_name": route_name,
			"device_id": "e2e-loader",
		},
		user=user,
		roles=roles,
		repository=repository,
		fulfilment_gateway=fulfilment_gateway,
		event_store=event_store,
	)
	for index, line in enumerate(frappe.get_doc("Transport Route", route_name).load_lines, start=1):
		record_load_scan(
			event={
				"event_id": f"{event_prefix}-load-scan-{index}-v1",
				"route_name": route_name,
				"scanned_code": line.item_code,
				"qty": float(line.qty),
				"device_id": "e2e-loader",
			},
			user=user,
			roles=roles,
			repository=repository,
			fulfilment_gateway=fulfilment_gateway,
			event_store=event_store,
		)
	complete_loading(
		event={
			"event_id": f"{event_prefix}-load-complete-v1",
			"route_name": route_name,
			"device_id": "e2e-loader",
		},
		user=user,
		roles=roles,
		repository=repository,
		fulfilment_gateway=fulfilment_gateway,
		event_store=event_store,
	)
	return dispatch_route(
		event={
			"event_id": f"{event_prefix}-dispatch-v1",
			"route_name": route_name,
			"device_id": "e2e-loader",
		},
		user=user,
		roles=roles,
		repository=repository,
		fulfilment_gateway=fulfilment_gateway,
		event_store=event_store,
	)


def _run_e2e_delivery(
	route_name: str,
	*,
	event_id: str = DEMO_E2E_POD_EVENT_ID,
	delivery_result: str = "Delivered",
	delivered_quantities: list[dict[str, Any]] | None = None,
	failure_reason: str = "",
	collection_amount: float = 10,
) -> dict[str, Any]:
	from neuradix_cassar_ops.operations.delivery_service import (
		FrappeDeliveryEventStore,
		record_proof_of_delivery,
	)
	from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway

	frappe.set_user("driver.one@neuradix.local")
	route = SQLMobileOperationsRepository().get_route(route_name=route_name)
	stop_name = str(route["stops"][0]["name"])
	signature = ""
	if delivery_result != "Failed":
		signature = "data:image/png;base64," + base64.b64encode(
			b"\x89PNG\r\n\x1a\nneuradix-e2e-signature"
		).decode()
	return record_proof_of_delivery(
		event={
			"event_id": event_id,
			"route_name": route_name,
			"stop_name": stop_name,
			"delivery_result": delivery_result,
			"recipient_name": "E2E Customer" if delivery_result != "Failed" else "",
			"signature_base64": signature,
			"latitude": 35.8997,
			"longitude": 14.5147,
			"collection_amount": collection_amount,
			"collection_mode": "Cash",
			"collection_reference": f"{event_id}-cash",
			"delivered_quantities": delivered_quantities or [],
			"failure_reason": failure_reason,
			"notes": f"{delivery_result} during automated UAT.",
			"device_id": "e2e-driver",
		},
		user=frappe.session.user,
		roles=frappe.get_roles(frappe.session.user),
		repository=SQLMobileOperationsRepository(),
		gateway=ERPNextFulfilmentGateway(),
		event_store=FrappeDeliveryEventStore(),
	)


def _assert_existing_fulfilment_cycle(event) -> dict[str, Any]:
	route_status = frappe.db.get_value("Transport Route", event.transport_route, "status")
	if route_status != "Closed":
		raise AssertionError(f"Expected completed demo route, found status {route_status}.")
	for doctype, name in (
		("Delivery Note", event.delivery_note),
		("Sales Invoice", event.sales_invoice),
		("Payment Entry", event.payment_entry),
	):
		if not name or not frappe.db.exists(doctype, {"name": name, "docstatus": 1}):
			raise AssertionError(f"Missing submitted {doctype} for the complete demo cycle.")
	return {
		"status": "already_complete",
		"transport_route": event.transport_route,
		"delivery_note": event.delivery_note,
		"sales_invoice": event.sales_invoice,
		"payment_entry": event.payment_entry,
		"delivery_event": DEMO_E2E_POD_EVENT_ID,
		"route_status": route_status,
	}


def _assert_local_seed_allowed() -> None:
	site_name = str(getattr(frappe.local, "site", "") or "").strip()
	if site_name == "neuradix-cassar.localhost" or site_name.endswith((".localhost", ".local")):
		return
	raise RuntimeError(
		f"Operations demo seed is local/UAT-only; refusing site '{site_name or '<unknown>'}'."
	)


def _get_demo_operations_password() -> str:
	"""Read the caller-supplied UAT password only when seeding is requested."""
	password = os.environ.get("NEURADIX_OPS_DEMO_PASSWORD", "")
	if not password.strip():
		raise RuntimeError("Set NEURADIX_OPS_DEMO_PASSWORD before seeding local operations users.")
	return password


def _ensure_operations_user(spec: DemoOperationsUser, password: str) -> None:
	name_parts = spec.full_name.split(maxsplit=1)
	if frappe.db.exists("User", spec.email):
		user = frappe.get_doc("User", spec.email)
	else:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": spec.email,
				"first_name": name_parts[0],
				"last_name": name_parts[1] if len(name_parts) > 1 else "",
				"full_name": spec.full_name,
				"username": spec.email.split("@", 1)[0],
				"send_welcome_email": 0,
				"enabled": 1,
				"user_type": "System User",
			}
		)
		user.insert(ignore_permissions=True)

	existing_roles = {row.role for row in user.roles}
	missing_roles = [role for role in spec.roles if role not in existing_roles]
	user_changed = False
	if not user.enabled:
		user.enabled = 1
		user_changed = True
	if user.user_type != "System User":
		user.user_type = "System User"
		user_changed = True
	for role in missing_roles:
		user.append("roles", {"role": role})
	if user_changed or missing_roles:
		user.save(ignore_permissions=True)
	update_password(spec.email, password, logout_all_sessions=False)


def _ensure_drivers() -> list[str]:
	driver_users = DEMO_OPERATIONS_USERS[6:]
	driver_names: list[str] = []
	for index, user_spec in enumerate(driver_users, start=1):
		driver_name = frappe.db.get_value("Driver", {"user": user_spec.email}, "name")
		if driver_name:
			driver = frappe.get_doc("Driver", driver_name)
		else:
			driver = frappe.get_doc(
				{
					"doctype": "Driver",
					"full_name": user_spec.full_name,
					"status": "Active",
					"user": user_spec.email,
					"cell_number": f"7900000{index}",
					"license_number": f"OPS-DEMO-LIC-{index}",
				}
			)
			driver.insert(ignore_permissions=True)
		driver.status = "Active"
		driver.user = user_spec.email
		driver.save(ignore_permissions=True)
		driver_names.append(str(driver.name))
	return driver_names


def _ensure_vehicles(company: str, transit_warehouse: str) -> list[str]:
	vehicles: list[str] = []
	for index in range(1, 3):
		license_plate = f"OPS-DEMO-{index:02d}"
		if frappe.db.exists("Vehicle", license_plate):
			vehicle = frappe.get_doc("Vehicle", license_plate)
		else:
			vehicle = frappe.get_doc(
				{
					"doctype": "Vehicle",
					"license_plate": license_plate,
					"make": "Iveco",
					"model": "Daily Demo",
					"last_odometer": 12000 + (index * 1000),
					"fuel_type": "Diesel",
					"uom": "Litre",
					"company": company,
					"color": "White",
				}
			)
			vehicle.insert(ignore_permissions=True)
		vehicle.company = company
		vehicle.custom_transit_warehouse = transit_warehouse
		vehicle.save(ignore_permissions=True)
		vehicles.append(str(vehicle.name))
	return vehicles


def _ensure_operations_warehouses(company: str) -> dict[str, str]:
	parent = frappe.db.get_value(
		"Warehouse",
		{"company": company, "is_group": 1, "parent_warehouse": ["is", "not set"]},
		"name",
	)
	if not parent:
		raise RuntimeError(f"Company {company} has no root warehouse.")
	warehouses = {}
	for key, warehouse_name in (
		("transit", "Goods In Transit"),
		("returns", "Delivery Returns"),
		("damaged", "Delivery Damaged"),
	):
		name = frappe.db.get_value(
			"Warehouse",
			{"company": company, "warehouse_name": warehouse_name},
			"name",
		)
		if not name:
			warehouse = frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": warehouse_name,
					"company": company,
					"parent_warehouse": parent,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
			name = warehouse.name
		warehouses[key] = str(name)
	return warehouses


def _ensure_operations_settings(
	*,
	company: str,
	source_warehouse: str,
	transit_warehouse: str,
	return_warehouse: str,
	damaged_warehouse: str,
) -> None:
	settings = frappe.get_doc("Cassar Operations Settings")
	settings.default_company = company
	settings.default_source_warehouse = source_warehouse
	settings.default_vehicle_transit_warehouse = transit_warehouse
	settings.default_return_warehouse = return_warehouse
	settings.default_damaged_warehouse = damaged_warehouse
	settings.checker_required = 1
	settings.checker_must_differ_from_picker = 1
	settings.invoice_on_dispatch = 1
	settings.require_signature = 1
	settings.require_gps = 0
	settings.enable_driver_collections = 1
	settings.default_collection_mode = "Cash"
	settings.save(ignore_permissions=True)


def _ensure_item_barcodes() -> None:
	for item_code, barcode in DEMO_ITEM_BARCODES.items():
		item = frappe.get_doc("Item", item_code)
		if any(row.barcode == barcode for row in item.barcodes):
			continue
		item.append("barcodes", {"barcode": barcode, "barcode_type": "EAN-13"})
		item.save(ignore_permissions=True)


def _ensure_operations_orders(company: str) -> list[str]:
	repository = SQLNeuradixRepository()
	taxes_template = frappe.db.get_single_value("Neuradix POS Settings", "sales_taxes_template")
	taxes = repository.get_sales_taxes(taxes_template) if taxes_template else []
	unit_items = [item for item in DEMO_ITEMS if item.uom == "Unit"]
	orders: list[str] = []

	with _suspend_insight_trade_agreement_validate("sales_order"):
		for index in range(8):
			client_order_id = f"{DEMO_ORDER_PREFIX}-{index + 1}"
			existing = repository.find_sync_record(client_order_id)
			if existing and existing.sales_order and frappe.db.exists(
				"Sales Order",
				{"name": existing.sales_order, "docstatus": 1},
			):
				orders.append(str(existing.sales_order))
				continue
			if existing:
				frappe.delete_doc(
					"Neuradix POS Sync Log",
					existing.name,
					ignore_permissions=True,
					force=True,
				)

			customer_seed = DEMO_CUSTOMERS[index % len(DEMO_CUSTOMERS)]
			customer = frappe.db.get_value(
				"Customer",
				{"custom_customer_id": customer_seed.custom_customer_id},
				"name",
			)
			if not customer:
				raise RuntimeError(f"Missing seeded customer {customer_seed.custom_customer_id}.")
			customer_doc = frappe.get_doc("Customer", customer)
			item_a = unit_items[index % len(unit_items)]
			item_b = unit_items[(index + 1) % len(unit_items)]
			sync_record = repository.create_sync_record(
				client_order_id=client_order_id,
				customer=customer,
				request_hash=f"ops-demo-{index + 1}",
				client_platform="operations-demo-seed",
				payload_json="{}",
			)
			order_name = repository.create_submitted_sales_order(
				{
					"client_order_id": client_order_id,
					"customer": customer,
					"hub_manager": getattr(customer_doc, "hub_manager", "") or "Administrator",
					"transaction_date": nowdate(),
					"delivery_date": add_days(nowdate(), _delivery_offset_for_order(index)),
					"mode_of_payment": "Cash",
					"notes": f"Neuradix Ops demo warehouse order {index + 1}",
					"items": [
						{
							"item_code": item_a.item_code,
							"item_name": item_a.item_name,
							"qty": 1 + (index % 2),
							"uom": item_a.uom,
							"rate": item_a.base_price,
							"notes": "Operations demo pick line",
						},
						{
							"item_code": item_b.item_code,
							"item_name": item_b.item_name,
							"qty": 1,
							"uom": item_b.uom,
							"rate": item_b.base_price,
							"notes": "Operations demo pick line",
						},
					],
					"order_taxes": taxes,
				}
			)
			repository.mark_sync_record_submitted(sync_record.name, order_name)
			orders.append(order_name)

	if len(orders) != 8:
		raise RuntimeError(f"Expected 8 operations demo orders, found {len(orders)}.")
	return orders


def _delivery_offset_for_order(order_index: int) -> int:
	for spec in DEMO_PLAN_SPECS:
		if any(order_index in route_indexes for route_indexes in spec.route_order_indexes):
			return spec.day_offset
	raise ValueError(f"Order index {order_index} is not assigned to a demo transport plan.")


def _ensure_transport_plans(
	*,
	company: str,
	warehouse: str,
	orders: list[str],
	drivers: list[str],
	vehicles: list[str],
) -> tuple[list[str], dict[str, list[str]]]:
	plans: list[str] = []
	routes_by_plan: dict[str, list[str]] = {}
	route_counter = 0
	for spec in DEMO_PLAN_SPECS:
		marker = f"{DEMO_PLAN_MARKER} {spec.code}"
		plan_name = frappe.db.get_value("Transport Plan", {"notes": marker}, "name")
		if plan_name:
			plan = frappe.get_doc("Transport Plan", plan_name)
		else:
			plan = frappe.get_doc(
				{
					"doctype": "Transport Plan",
					"company": company,
					"plan_date": add_days(nowdate(), spec.day_offset),
					"source_warehouse": warehouse,
					"status": "Draft",
					"notes": marker,
				}
			)
			plan.insert(ignore_permissions=True)

		plan.company = company
		plan.plan_date = add_days(nowdate(), spec.day_offset)
		plan.source_warehouse = warehouse
		plan.notes = marker
		plan.save(ignore_permissions=True)

		existing_routes = frappe.get_all(
			"Transport Route",
			filters={"transport_plan": plan.name, "docstatus": ["<", 2]},
			order_by="creation asc",
			pluck="name",
		)
		plan_routes: list[str] = []
		for route_index, order_indexes in enumerate(spec.route_order_indexes):
			driver = drivers[route_counter % len(drivers)]
			vehicle = vehicles[route_counter % len(vehicles)]
			route_counter += 1
			if route_index < len(existing_routes):
				route = frappe.get_doc("Transport Route", existing_routes[route_index])
			else:
				route = frappe.get_doc(
					{
						"doctype": "Transport Route",
						"delivery_date": plan.plan_date,
						"transport_plan": plan.name,
						"status": "Created",
					}
				)
				route.insert(ignore_permissions=True)

			route.delivery_date = plan.plan_date
			route.transport_plan = plan.name
			route.status = "Created"
			route.driver = driver
			route.vehicle = vehicle
			route.set("stops", [])
			route.set("stop_documents", [])

			grouped_orders = _group_orders_by_customer([orders[index] for index in order_indexes])
			for sequence, (customer, customer_orders) in enumerate(grouped_orders.items(), start=1):
				customer_row = frappe.db.get_value(
					"Customer",
					customer,
					["mobile_no", "primary_address"],
					as_dict=True,
				) or {}
				primary_address = customer_row.get("primary_address")
				ship_to = (
					primary_address
					if primary_address and frappe.db.exists("Address", primary_address)
					else None
				)
				route.append(
					"stops",
					{
						"sequence": sequence,
						"customer": customer,
						"ship_to": ship_to,
						"phone": customer_row.get("mobile_no"),
						"status": "Queued",
						"special_instructions": "Operations demo delivery stop",
						"service_time_min": 15,
						"doc_count": len(customer_orders),
					},
				)
			route.customers = len(grouped_orders)
			route.sales_orders = len(order_indexes)
			route.save(ignore_permissions=True)

			for stop, customer_orders in zip(route.stops, grouped_orders.values(), strict=True):
				for order_name in customer_orders:
					order = frappe.db.get_value(
						"Sales Order",
						order_name,
						["customer", "grand_total"],
						as_dict=True,
					)
					route.append(
						"stop_documents",
						{
							"stop": stop.name,
							"ref_type": "Sales Order",
							"ref_name": order_name,
							"customer": order.customer,
							"amount_due": order.grand_total,
							"outstanding_amount": order.grand_total,
							"delivery_status": "Undelivered",
							"payment_status": "Unpaid",
							"notes": "Operations demo order",
						},
					)
			route.save(ignore_permissions=True)
			plan_routes.append(str(route.name))

		plan.reload()
		plan.status = "Planned"
		plan.total_routes = len(plan_routes)
		plan.total_stops = sum(
			frappe.db.count("Transport Route Stop", {"parent": route_name})
			for route_name in plan_routes
		)
		plan.save(ignore_permissions=True)
		plans.append(str(plan.name))
		routes_by_plan[str(plan.name)] = plan_routes
	return plans, routes_by_plan


def _group_orders_by_customer(order_names: list[str]) -> dict[str, list[str]]:
	rows = frappe.db.sql(
		"""
		SELECT name, customer
		FROM `tabSales Order`
		WHERE name IN %(order_names)s
		""",
		{"order_names": tuple(order_names)},
		as_dict=True,
	)
	customers_by_order = {str(row.name): str(row.customer) for row in rows}
	grouped: dict[str, list[str]] = {}
	for order_name in order_names:
		customer = customers_by_order.get(order_name)
		if not customer:
			raise RuntimeError(f"Missing submitted Sales Order {order_name}.")
		grouped.setdefault(customer, []).append(order_name)
	return grouped


def _ensure_pick_lists(routes_by_plan: dict[str, list[str]]) -> list[str]:
	pick_lists: list[str] = []
	order_index = 0
	for plan_name, route_names in routes_by_plan.items():
		for route_name in route_names:
			order_names = frappe.get_all(
				"Route Stop Document",
				filters={
					"parent": route_name,
					"parenttype": "Transport Route",
					"parentfield": "stop_documents",
					"ref_type": "Sales Order",
				},
				order_by="idx asc",
				pluck="ref_name",
			)
			for order_name in order_names:
				pick_list_name = _find_demo_pick_list(
					plan_name=plan_name,
					route_name=route_name,
					order_name=order_name,
				)
				if pick_list_name:
					pick_list = frappe.get_doc("Pick List", pick_list_name)
				else:
					pick_list = create_pick_list(order_name)
				if pick_list.docstatus == 1:
					pick_lists.append(str(pick_list.name))
					order_index += 1
					continue
				if not pick_list.locations:
					raise RuntimeError(f"Sales Order {order_name} produced an empty Pick List.")
				pick_list.custom_transport_plan = plan_name
				pick_list.custom_transport_route = route_name
				_apply_pick_state(pick_list, order_index)
				pick_list.save(ignore_permissions=True)
				pick_lists.append(str(pick_list.name))
				order_index += 1
	return pick_lists


def _find_demo_pick_list(*, plan_name: str, route_name: str, order_name: str) -> str | None:
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT pick_list.name
		FROM `tabPick List` pick_list
		INNER JOIN `tabPick List Item` item ON item.parent = pick_list.name
		WHERE pick_list.docstatus IN (0, 1)
		  AND pick_list.custom_transport_plan = %(plan_name)s
		  AND pick_list.custom_transport_route = %(route_name)s
		  AND item.sales_order = %(order_name)s
		ORDER BY pick_list.docstatus ASC, pick_list.creation ASC
		LIMIT 1
		""",
		{
			"plan_name": plan_name,
			"route_name": route_name,
			"order_name": order_name,
		},
		as_dict=True,
	)
	return str(rows[0].name) if rows else None


def _apply_pick_state(pick_list, order_index: int) -> None:
	for row in pick_list.locations:
		row.picked_qty = 0
	if order_index in {2, 4}:
		pick_list.custom_picker = (
			DEMO_OPERATIONS_USERS[2].email
			if order_index == 2
			else DEMO_OPERATIONS_USERS[3].email
		)
		pick_list.custom_picking_status = "In Progress"
		pick_list.locations[0].picked_qty = min(float(pick_list.locations[0].qty or 0), 1.0)
	elif order_index in {3, 6}:
		pick_list.custom_picker = (
			DEMO_OPERATIONS_USERS[2].email
			if order_index == 3
			else DEMO_OPERATIONS_USERS[3].email
		)
		pick_list.custom_picking_status = "Done"
		for row in pick_list.locations:
			row.picked_qty = row.qty
	else:
		pick_list.custom_picker = None
		pick_list.custom_picking_status = "Not Claimed"


def _count_seeded_records() -> dict[str, int]:
	plan_names = frappe.get_all(
		"Transport Plan",
		filters={"notes": ["like", f"{DEMO_PLAN_MARKER}%"]},
		pluck="name",
	)
	return {
		"users": _sql_count(
			"SELECT COUNT(*) AS total FROM `tabUser` WHERE name IN %(names)s",
			{"names": tuple(user.email for user in DEMO_OPERATIONS_USERS)},
		),
		"sales_orders": _sql_count(
			"""
			SELECT COUNT(*) AS total
			FROM `tabNeuradix POS Sync Log` sync_log
			INNER JOIN `tabSales Order` sales_order
				ON sales_order.name = sync_log.sales_order AND sales_order.docstatus = 1
			WHERE sync_log.client_order_id LIKE %(prefix)s
			""",
			{"prefix": f"{DEMO_ORDER_PREFIX}-%"},
		),
		"transport_plans": len(plan_names),
		"transport_routes": _sql_count(
			"SELECT COUNT(*) AS total FROM `tabTransport Route` WHERE transport_plan IN %(plans)s",
			{"plans": tuple(plan_names)},
		),
		"pick_lists": _sql_count(
			"""
			SELECT COUNT(*) AS total
			FROM `tabPick List`
			WHERE docstatus IN (0, 1) AND custom_transport_plan IN %(plans)s
			""",
			{"plans": tuple(plan_names)},
		),
		"barcodes": _sql_count(
			"SELECT COUNT(*) AS total FROM `tabItem Barcode` WHERE barcode IN %(barcodes)s",
			{"barcodes": tuple(DEMO_ITEM_BARCODES.values())},
		),
	}


def _sql_count(query: str, values: dict[str, Any]) -> int:
	rows = frappe.db.sql(query, values=values, as_dict=True)
	return int(rows[0].total or 0)


__all__ = [
	"assert_demo_delivery_exception_cycles",
	"assert_demo_fulfilment_cycle",
	"assert_demo_insight_planning_cycle",
	"assert_demo_operations_ready",
	"seed_demo_operations_data",
]

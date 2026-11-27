from __future__ import annotations

import importlib
import json
from typing import Any

import frappe
from frappe.exceptions import TimestampMismatchError

from neuradix_cassar_ops.operations.exceptions import OperationsValidationError


INSIGHT_FAILURE_REASONS = frozenset(
	{"Closed store", "Customer refused", "No stock", "Access issue", "Other"}
)


class InsightTransportGateway:
	"""Narrow boundary around Insight's public transport and picking interfaces."""

	@staticmethod
	def capabilities() -> dict[str, bool]:
		return {
			"transport_planning": bool(frappe.db.exists("DocType", "Transport Plan")),
			"route_optimization": bool(frappe.db.exists("DocType", "Route Optimization Run")),
			"picking_hub": bool(frappe.db.exists("DocType", "Pick List")),
		}

	@staticmethod
	def create_plan(*, plan_date: str, company: str, source_warehouse: str, notes: str = "") -> dict:
		plan = frappe.get_doc(
			{
				"doctype": "Transport Plan",
				"plan_date": plan_date,
				"company": company,
				"source_warehouse": source_warehouse,
				"notes": notes,
				"status": "Draft",
			}
		)
		# Access is enforced by the facade against the same roles as Insight's planner API.
		plan.insert(ignore_permissions=True)
		return InsightTransportGateway.serialize_plan(plan)

	@staticmethod
	def preview_routes(
		*,
		plan_name: str,
		vehicles_json: str | None = None,
		nl_instructions: str | None = None,
	) -> dict[str, Any]:
		module = importlib.import_module("insight.api.auto_generate_routes")
		result = module.preview_auto_generate_routes(
			plan_name=plan_name,
			vehicles_json=vehicles_json,
			nl_instructions=nl_instructions,
		)
		if result.get("clusters") or int(result.get("total_stops") or 0):
			return result
		return InsightTransportGateway._preview_routes_with_frappe_compatibility(
			module=module,
			plan_name=plan_name,
			vehicles_json=vehicles_json,
			parsed_constraints=list(result.get("parsed_constraints") or []),
			fallback=result,
		)

	@staticmethod
	def _preview_routes_with_frappe_compatibility(
		*,
		module,
		plan_name: str,
		vehicles_json: str | None,
		parsed_constraints: list[dict],
		fallback: dict[str, Any],
	) -> dict[str, Any]:
		"""Use Insight's planner with a Frappe-v16-compatible candidate query."""
		stops, skipped_no_coordinates = InsightTransportGateway._gather_plan_stops(plan_name)
		if not stops:
			return fallback

		warnings = [
			warning
			for warning in list(fallback.get("warnings") or [])
			if "No undelivered Sales Orders" not in str(warning)
		]
		if skipped_no_coordinates:
			warnings.append(
				f"{skipped_no_coordinates} Sales Order stop(s) skipped because the ship-to address has no geocoordinates."
			)
		vehicles = module._load_vehicles(vehicles_json, warnings)
		drivers = module._load_drivers()
		total_weight_kg = sum(float(stop.get("planned_weight_kg") or 0) for stop in stops)

		clustering = importlib.import_module("insight.transport.clustering")
		if vehicles:
			num_clusters = clustering.compute_num_clusters(total_weight_kg, vehicles)
			num_clusters = max(1, min(num_clusters, len(vehicles)))
		else:
			num_clusters = max(1, (len(stops) + 14) // 15)
			warnings.append("No vehicles specified - route count estimated from stop count.")

		plan = frappe.get_doc("Transport Plan", plan_name)
		optimizer = importlib.import_module("insight.transport.optimizer")
		depot = optimizer._resolve_depot(plan)
		clusters = clustering.cluster_stops_geographic(stops, num_clusters, depot)
		if parsed_constraints:
			clusters, constraint_warnings = module._apply_constraints_to_clusters(
				clusters,
				parsed_constraints,
			)
			warnings.extend(constraint_warnings)
		assignments = clustering.assign_vehicles_to_clusters(clusters, vehicles, drivers)

		cold_start = module._is_cold_start()
		if cold_start:
			warnings.append(
				"No historical route data available - using geographic clustering (Phase 1a)."
			)
		cluster_summaries = []
		for assignment in assignments:
			cluster_stops = list(assignment["stops"])
			zones = sorted(
				{
					module._get_stop_zone(stop)
					for stop in cluster_stops
					if module._get_stop_zone(stop) != "DEFAULT"
				}
			)
			cluster_summaries.append(
				{
					"cluster_idx": assignment["cluster_idx"],
					"stops": cluster_stops,
					"vehicle": assignment["vehicle"],
					"driver": assignment["driver"],
					"total_weight_kg": assignment["total_weight_kg"],
					"stop_count": len(cluster_stops),
					"zones": zones,
				}
			)
		return {
			"clusters": cluster_summaries,
			"parsed_constraints": parsed_constraints,
			"warnings": warnings,
			"total_stops": len(stops),
			"total_weight_kg": total_weight_kg,
			"cold_start": cold_start,
		}

	@staticmethod
	def _gather_plan_stops(plan_name: str) -> tuple[list[dict[str, Any]], int]:
		plan = frappe.get_doc("Transport Plan", plan_name)
		conditions = [
			"sales_order.docstatus = 1",
			"(COALESCE(sales_order.per_delivered, 0) < 100 OR COALESCE(sales_order.per_billed, 0) < 100)",
			"sales_order.delivery_date = %(plan_date)s",
		]
		values: dict[str, Any] = {"plan_date": plan.plan_date}
		if plan.company:
			conditions.append("sales_order.company = %(company)s")
			values["company"] = plan.company
		rows = frappe.db.sql(
			f"""
			SELECT
				sales_order.name AS ref_name,
				'Sales Order' AS ref_type,
				sales_order.customer,
				sales_order.shipping_address_name AS ship_to,
				COALESCE((
					SELECT SUM(item.qty * item.weight_per_unit)
					FROM `tabSales Order Item` item
					WHERE item.parent = sales_order.name
				), 0.0) AS planned_weight_kg,
				address.custom_latitude AS latitude,
				address.custom_longitude AS longitude
			FROM `tabSales Order` sales_order
			LEFT JOIN `tabAddress` address
				ON address.name = sales_order.shipping_address_name
			WHERE {" AND ".join(conditions)}
			ORDER BY sales_order.customer, sales_order.name
			""",
			values,
			as_dict=True,
		)
		stops = []
		skipped_no_coordinates = 0
		for row in rows:
			if row.latitude is None or row.longitude is None:
				skipped_no_coordinates += 1
				continue
			stop = dict(row)
			stop["latitude"] = float(row.latitude)
			stop["longitude"] = float(row.longitude)
			stops.append(stop)
		return stops, skipped_no_coordinates

	@staticmethod
	def apply_routes(*, plan_name: str, clusters: list[dict], enhance: bool = False) -> dict[str, Any]:
		existing_routes = frappe.db.sql(
			"""
			SELECT route
			FROM `tabTransport Plan Route`
			WHERE parent = %(plan_name)s
				AND parenttype = 'Transport Plan'
				AND parentfield = 'routes'
				AND COALESCE(route, '') != ''
			LIMIT 1
			""",
			{"plan_name": plan_name},
			as_dict=True,
		)
		if existing_routes:
			raise OperationsValidationError(
				"This Transport Plan already has routes. Review or cancel them before generating again."
			)
		module = importlib.import_module("insight.api.auto_generate_routes")
		try:
			result = module.apply_auto_generate_routes(
				plan_name=plan_name,
				clusters_json=json.dumps(clusters),
				trigger_enhance=1 if enhance else 0,
			)
		except TimestampMismatchError:
			created_routes = InsightTransportGateway._get_plan_routes(plan_name)
			if not created_routes:
				raise
			frappe.db.set_value("Transport Plan", plan_name, "status", "Planned")
			result = {
				"created_routes": created_routes,
				"warnings": [
					"Insight created the routes; Neuradix refreshed the concurrently updated Transport Plan."
				],
			}
		InsightTransportGateway._ensure_route_source_links(
			created_routes=list(result.get("created_routes") or []),
			clusters=clusters,
		)
		return result

	@staticmethod
	def _get_plan_routes(plan_name: str) -> list[str]:
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

	@staticmethod
	def _ensure_route_source_links(*, created_routes: list[str], clusters: list[dict]) -> None:
		"""Normalize Insight route output to its current Route Stop Document schema."""
		route_clusters = [cluster for cluster in clusters if cluster.get("stops")]
		if len(created_routes) != len(route_clusters):
			raise OperationsValidationError(
				"Insight returned an unexpected route count; source documents were not linked."
			)
		for route_name, cluster in zip(created_routes, route_clusters, strict=True):
			route = frappe.get_doc("Transport Route", route_name)
			cluster_stops = list(cluster.get("stops") or [])
			if len(route.stops) != len(cluster_stops):
				raise OperationsValidationError(
					f"Insight route {route_name} does not match its confirmed stop proposal."
				)
			existing = {
				(str(row.ref_type), str(row.ref_name)) for row in route.stop_documents
			}
			for route_stop, source in zip(route.stops, cluster_stops, strict=True):
				ref_type = str(source.get("ref_type") or "")
				ref_name = str(source.get("ref_name") or "")
				if not ref_type or not ref_name or (ref_type, ref_name) in existing:
					continue
				if ref_type != "Sales Order":
					raise OperationsValidationError(
						f"Unsupported Insight route source {ref_type} {ref_name}."
					)
				snapshot = frappe.db.get_value(
					"Sales Order",
					ref_name,
					["customer", "grand_total"],
					as_dict=True,
				)
				if not snapshot:
					raise OperationsValidationError(
						f"Insight route source Sales Order {ref_name} does not exist."
					)
				route.append(
					"stop_documents",
					{
						"stop": route_stop.name,
						"ref_type": ref_type,
						"ref_name": ref_name,
						"customer": snapshot.customer,
						"amount_due": snapshot.grand_total,
						"outstanding_amount": snapshot.grand_total,
						"delivery_status": "Undelivered",
						"payment_status": "Unpaid",
					},
				)
				existing.add((ref_type, ref_name))
			route.customers = len({str(stop.customer) for stop in route.stops})
			route.sales_orders = sum(
				1 for row in route.stop_documents if row.ref_type == "Sales Order"
			)
			route.save(ignore_permissions=True)

	@staticmethod
	def create_pick_lists(*, plan_name: str, routes: list[str] | None = None) -> dict[str, list[str]]:
		selected_routes = routes or [
			row.route
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
		if not selected_routes:
			frappe.throw("No routes selected.", frappe.ValidationError)

		route_params = {f"route_{index}": route for index, route in enumerate(selected_routes)}
		route_placeholders = ", ".join(f"%(route_{index})s" for index in range(len(selected_routes)))
		existing_rows = frappe.db.sql(
			f"""
			SELECT name, custom_transport_route
			FROM `tabPick List`
			WHERE docstatus < 2
				AND custom_transport_plan = %(plan_name)s
				AND custom_transport_route IN ({route_placeholders})
			ORDER BY creation ASC
			""",
			{"plan_name": plan_name, **route_params},
			as_dict=True,
		)
		existing: dict[str, list[str]] = {}
		for row in existing_rows:
			existing.setdefault(row.custom_transport_route, []).append(row.name)

		missing_routes = [route for route in selected_routes if route not in existing]
		if not missing_routes:
			return existing

		module = importlib.import_module(
			"insight.transport_management.doctype.transport_plan.transport_plan"
		)
		created = module.create_pick_lists_from_plan(
			plan_name=plan_name,
			routes_json=json.dumps(missing_routes),
		)
		for route_name, pick_list_names in created.items():
			for pick_list_name in pick_list_names:
				pick_list = frappe.get_doc("Pick List", pick_list_name)
				if hasattr(pick_list, "custom_transport_plan"):
					pick_list.custom_transport_plan = plan_name
				if hasattr(pick_list, "custom_transport_route"):
					pick_list.custom_transport_route = route_name
				pick_list.save()
		return {**existing, **created}

	@staticmethod
	def claim_pick_list(pick_list_name: str) -> str:
		module = importlib.import_module("insight.insight.page.picking_hub.picking_hub")
		return module.claim_pick_list(pick_list_name)

	@staticmethod
	def record_pick_scan(*, pick_list: str, scanned_code: str, qty: float) -> dict[str, Any]:
		module = importlib.import_module("insight.insight.page.picking_hub.picking_hub")
		return module.record_pick_scan(pick_list=pick_list, scanned_code=scanned_code, qty=qty)

	@staticmethod
	def finalize_pick_list(pick_list_name: str) -> dict[str, Any]:
		module = importlib.import_module("insight.insight.page.picking_hub.picking_hub")
		return module.finalize_pick_list(pick_list_name)

	@staticmethod
	def complete_route_stop(*, route_name: str, stop_name: str, delivered_notes: str = "") -> dict[str, Any]:
		return InsightTransportGateway.record_route_stop_outcome(
			route_name=route_name,
			stop_name=stop_name,
			delivery_result="Delivered",
			delivered_notes=delivered_notes,
			close_when_all_terminal=True,
		)

	@staticmethod
	def record_route_stop_outcome(
		*,
		route_name: str,
		stop_name: str,
		delivery_result: str,
		failure_reason: str = "",
		delivered_notes: str = "",
		payment_entry: str = "",
		payment_mode: str = "",
		close_when_all_terminal: bool = False,
	) -> dict[str, Any]:
		"""Persist execution state on Insight's route without changing Insight code."""
		if delivery_result not in {"Delivered", "Partially Delivered", "Failed"}:
			raise OperationsValidationError(f"Unsupported delivery result {delivery_result}.")
		route = frappe.get_doc("Transport Route", route_name)
		stop = next((row for row in route.stops if row.name == stop_name), None)
		if stop is None:
			raise OperationsValidationError(
				f"Route stop {stop_name} does not belong to Transport Route {route_name}."
			)

		stop.status = delivery_result
		stop.delivered_notes = delivered_notes
		stop.failure_reason = (
			failure_reason if failure_reason in INSIGHT_FAILURE_REASONS else "Other"
		) if delivery_result == "Failed" else ""
		updated_documents = []
		for document in route.stop_documents:
			if document.stop != stop_name:
				continue
			document.delivery_status = "Delivered" if delivery_result == "Delivered" else "Undelivered"
			if delivered_notes:
				document.notes = delivered_notes
			if payment_entry:
				document.payment_status = "Paid"
				document.payment_mode = payment_mode
				document.customer_payment_ref = payment_entry
			updated_documents.append(document.ref_name)

		terminal_statuses = {"Delivered", "Partially Delivered", "Failed"}
		if close_when_all_terminal and route.stops and all(
			row.status in terminal_statuses for row in route.stops
		):
			route.status = "Closed"
		elif route.status in {"", "Not started", "Created", "Picked", "Loading", "Loaded", "Invoiced"}:
			route.status = "Dispatched"
		route.save(ignore_permissions=True)
		return {
			"route_name": route.name,
			"stop_name": stop.name,
			"route_status": route.status,
			"stop_status": stop.status,
			"updated_documents": updated_documents,
		}

	@staticmethod
	def serialize_plan(plan) -> dict[str, Any]:
		return {
			"name": plan.name,
			"plan_date": str(plan.plan_date or ""),
			"company": plan.company or "",
			"source_warehouse": plan.source_warehouse or "",
			"status": plan.status or "Draft",
			"notes": plan.notes or "",
			"total_routes": int(plan.total_routes or 0),
			"total_stops": int(plan.total_stops or 0),
		}

from __future__ import annotations

from typing import Any

import frappe

from neuradix_cassar_ops.operations.exceptions import OperationsValidationError


class SQLMobileOperationsRepository:
	"""Read-optimized mobile queues backed by parameterized frappe.db.sql calls."""

	def list_transport_plans(self, *, plan_date: str | None = None, limit: int = 100) -> list[dict]:
		limit = max(1, min(int(limit), 200))
		params: dict[str, Any] = {"limit": limit}
		date_condition = ""
		if plan_date:
			date_condition = "AND transport_plan.plan_date = %(plan_date)s"
			params["plan_date"] = plan_date
		return frappe.db.sql(
			f"""
			SELECT
				transport_plan.name,
				transport_plan.plan_date,
				transport_plan.company,
				transport_plan.source_warehouse,
				transport_plan.status,
				transport_plan.total_routes,
				transport_plan.total_stops,
				transport_plan.modified
			FROM `tabTransport Plan` transport_plan
			WHERE transport_plan.docstatus < 2
				{date_condition}
			ORDER BY transport_plan.plan_date DESC, transport_plan.modified DESC
			LIMIT %(limit)s
			""",
			params,
			as_dict=True,
		)

	def list_pick_lists(
		self,
		*,
		user: str,
		queue: str = "available",
		status: str | None = None,
		limit: int = 100,
	) -> list[dict]:
		limit = max(1, min(int(limit), 200))
		params: dict[str, Any] = {"user": user, "limit": limit}
		conditions = ["pick_list.docstatus = 0"]
		if queue == "mine":
			conditions.append("pick_list.custom_picker = %(user)s")
		elif queue == "checking":
			conditions.append("pick_list.custom_picking_status = 'Done'")
			conditions.append(
				"COALESCE(pick_list.custom_checking_status, 'Pending') IN ('Pending', 'In Progress')"
			)
		else:
			conditions.append("COALESCE(pick_list.custom_picker, '') = ''")
			conditions.append("COALESCE(pick_list.custom_picking_status, '') IN ('', 'Not Claimed')")
		if status:
			conditions.append("pick_list.custom_picking_status = %(status)s")
			params["status"] = status

		rows = frappe.db.sql(
			f"""
			SELECT
				pick_list.name,
				pick_list.company,
				COALESCE(pick_list.customer, '') AS customer,
				COALESCE(pick_list.custom_transport_plan, '') AS transport_plan,
				COALESCE(pick_list.custom_transport_route, '') AS transport_route,
				COALESCE(pick_list.custom_picking_status, 'Not Claimed') AS picking_status,
				COALESCE(pick_list.custom_picker, '') AS picker,
				COALESCE(pick_list.custom_checking_status, 'Pending') AS checking_status,
				COALESCE(pick_list.custom_checker, '') AS checker,
				COUNT(location.name) AS line_count,
				COALESCE(SUM(location.qty), 0) AS total_qty,
				COALESCE(SUM(location.picked_qty), 0) AS picked_qty,
				pick_list.modified
			FROM `tabPick List` pick_list
			LEFT JOIN `tabPick List Item` location ON location.parent = pick_list.name
			WHERE {' AND '.join(conditions)}
			GROUP BY pick_list.name
			ORDER BY pick_list.creation ASC
			LIMIT %(limit)s
			""",
			params,
			as_dict=True,
		)
		for row in rows:
			row["line_count"] = int(row.get("line_count") or 0)
			row["total_qty"] = float(row.get("total_qty") or 0)
			row["picked_qty"] = float(row.get("picked_qty") or 0)
		return rows

	def get_pick_list(self, *, pick_list_name: str) -> dict:
		headers = frappe.db.sql(
			"""
			SELECT
				pick_list.name,
				pick_list.company,
				COALESCE(pick_list.customer, '') AS customer,
				COALESCE(pick_list.custom_transport_plan, '') AS transport_plan,
				COALESCE(pick_list.custom_transport_route, '') AS transport_route,
				COALESCE(pick_list.custom_picking_status, 'Not Claimed') AS picking_status,
				COALESCE(pick_list.custom_picker, '') AS picker,
				COALESCE(pick_list.custom_checking_status, 'Pending') AS checking_status,
				COALESCE(pick_list.custom_checker, '') AS checker,
				COALESCE(pick_list.custom_check_notes, '') AS check_notes,
				pick_list.custom_checked_at AS checked_at,
				pick_list.docstatus,
				pick_list.modified
			FROM `tabPick List` pick_list
			WHERE pick_list.name = %(pick_list)s AND pick_list.docstatus < 2
			""",
			{"pick_list": pick_list_name},
			as_dict=True,
		)
		if not headers:
			raise OperationsValidationError(f"Pick List {pick_list_name} does not exist.")
		lines = frappe.db.sql(
			"""
			SELECT
				location.name,
				location.idx,
				location.item_code,
				location.item_name,
				location.warehouse,
				location.uom,
				location.qty,
				location.picked_qty,
				location.batch_no,
				location.serial_no,
				COALESCE(GROUP_CONCAT(DISTINCT barcode.barcode ORDER BY barcode.idx), '') AS barcodes
			FROM `tabPick List Item` location
			LEFT JOIN `tabItem Barcode` barcode ON barcode.parent = location.item_code
			WHERE location.parent = %(pick_list)s
			GROUP BY location.name
			ORDER BY location.idx ASC
			""",
			{"pick_list": pick_list_name},
			as_dict=True,
		)
		for line in lines:
			line["qty"] = float(line.get("qty") or 0)
			line["picked_qty"] = float(line.get("picked_qty") or 0)
			line["barcodes"] = [value for value in str(line.get("barcodes") or "").split(",") if value]
		return {**headers[0], "lines": lines}

	def get_route_load_manifest(self, *, route_name: str) -> dict:
		route = self.get_route(route_name=route_name)
		pick_lists = frappe.db.sql(
			"""
			SELECT
				pick_list.name,
				pick_list.docstatus,
				COALESCE(pick_list.custom_picking_status, 'Not Claimed') AS picking_status,
				COALESCE(pick_list.custom_picker, '') AS picker,
				COALESCE(pick_list.custom_checking_status, 'Pending') AS checking_status,
				COALESCE(pick_list.custom_checker, '') AS checker,
				COUNT(location.name) AS line_count,
				COALESCE(SUM(location.picked_qty), 0) AS picked_qty,
				COALESCE(SUM(location.qty), 0) AS required_qty
			FROM `tabPick List` pick_list
			LEFT JOIN `tabPick List Item` location ON location.parent = pick_list.name
			WHERE pick_list.docstatus < 2
				AND pick_list.custom_transport_route = %(route_name)s
			GROUP BY pick_list.name
			ORDER BY pick_list.creation ASC
			""",
			{"route_name": route_name},
			as_dict=True,
		)
		for pick_list in pick_lists:
			pick_list["line_count"] = int(pick_list.get("line_count") or 0)
			pick_list["picked_qty"] = float(pick_list.get("picked_qty") or 0)
			pick_list["required_qty"] = float(pick_list.get("required_qty") or 0)

		lines = frappe.db.sql(
			"""
			SELECT
				load_line.name,
				load_line.idx,
				load_line.item_code,
				load_line.item_name,
				load_line.sales_order,
				load_line.stop,
				load_line.warehouse,
				load_line.uom,
				load_line.stock_uom,
				load_line.qty,
				load_line.picked_qty AS loaded_qty,
				COALESCE(load_line.status, 'Pending') AS status,
				COALESCE(GROUP_CONCAT(DISTINCT barcode.barcode ORDER BY barcode.idx), '') AS barcodes
			FROM `tabTransport Load Line` load_line
			LEFT JOIN `tabItem Barcode` barcode ON barcode.parent = load_line.item_code
			WHERE load_line.parent = %(route_name)s
				AND load_line.parenttype = 'Transport Route'
				AND load_line.parentfield = 'load_lines'
			GROUP BY load_line.name
			ORDER BY load_line.idx ASC
			""",
			{"route_name": route_name},
			as_dict=True,
		)
		for line in lines:
			line["qty"] = float(line.get("qty") or 0)
			line["loaded_qty"] = float(line.get("loaded_qty") or 0)
			line["barcodes"] = [
				value for value in str(line.get("barcodes") or "").split(",") if value
			]
		return {**route, "pick_lists": pick_lists, "load_lines": lines}

	def list_routes(
		self,
		*,
		delivery_date: str | None = None,
		limit: int = 100,
		user: str | None = None,
		driver_only: bool = False,
	) -> list[dict]:
		limit = max(1, min(int(limit), 200))
		params: dict[str, Any] = {"limit": limit}
		date_condition = ""
		if delivery_date:
			date_condition = "AND route.delivery_date = %(delivery_date)s"
			params["delivery_date"] = delivery_date
		driver_join = ""
		driver_condition = ""
		if driver_only:
			driver_join = "INNER JOIN `tabDriver` driver ON driver.name = route.driver"
			driver_condition = "AND driver.user = %(user)s"
			params["user"] = user or ""
		rows = frappe.db.sql(
			f"""
			SELECT
				route.name,
				route.transport_plan,
				route.delivery_date,
				COALESCE(route.vehicle, '') AS vehicle,
				COALESCE(route.driver, '') AS driver,
				route.status,
				COUNT(stop.name) AS stop_count,
				SUM(stop.status IN ('Delivered', 'Partially Delivered', 'Failed')) AS delivered_stops
			FROM `tabTransport Route` route
			{driver_join}
			LEFT JOIN `tabTransport Route Stop` stop ON stop.parent = route.name
			WHERE route.docstatus < 2 {date_condition} {driver_condition}
			GROUP BY route.name
			ORDER BY route.delivery_date DESC, route.name ASC
			LIMIT %(limit)s
			""",
			params,
			as_dict=True,
		)
		for row in rows:
			row["stop_count"] = int(row.get("stop_count") or 0)
			row["delivered_stops"] = int(row.get("delivered_stops") or 0)
		return rows

	def get_route(self, *, route_name: str) -> dict:
		headers = frappe.db.sql(
			"""
			SELECT
				route.name,
				route.transport_plan,
				route.delivery_date,
				COALESCE(route.vehicle, '') AS vehicle,
				COALESCE(route.driver, '') AS driver,
				COALESCE(driver.user, '') AS driver_user,
				COALESCE(vehicle.custom_transit_warehouse, '') AS vehicle_transit_warehouse,
				route.status
			FROM `tabTransport Route` route
			LEFT JOIN `tabDriver` driver ON driver.name = route.driver
			LEFT JOIN `tabVehicle` vehicle ON vehicle.name = route.vehicle
			WHERE route.name = %(route_name)s AND route.docstatus < 2
			LIMIT 1
			""",
			{"route_name": route_name},
			as_dict=True,
		)
		if not headers:
			raise OperationsValidationError(f"Transport Route {route_name} does not exist.")

		stops = frappe.db.sql(
			"""
			SELECT
				stop.name,
				stop.sequence,
				stop.customer,
				COALESCE(customer.customer_name, stop.customer) AS customer_name,
				COALESCE(stop.ship_to, '') AS ship_to,
				COALESCE(stop.contact_person, '') AS contact_person,
				COALESCE(stop.phone, '') AS phone,
				stop.status,
				COALESCE(stop.special_instructions, '') AS special_instructions,
				COALESCE(stop.delivered_notes, '') AS delivered_notes,
				COALESCE(stop.doc_count, 0) AS doc_count,
				COALESCE(stop.planned_amount_due, 0) AS planned_amount_due
			FROM `tabTransport Route Stop` stop
			LEFT JOIN `tabCustomer` customer ON customer.name = stop.customer
			WHERE stop.parent = %(route_name)s
				AND stop.parenttype = 'Transport Route'
				AND stop.parentfield = 'stops'
			ORDER BY stop.sequence ASC, stop.idx ASC
			""",
			{"route_name": route_name},
			as_dict=True,
		)
		documents = frappe.db.sql(
			"""
			SELECT
				document.name,
				document.stop,
				document.ref_type,
				document.ref_name,
				document.customer,
				COALESCE(document.amount_due, 0) AS amount_due,
				COALESCE(document.outstanding_amount, 0) AS outstanding_amount,
				COALESCE(document.delivery_status, 'Undelivered') AS delivery_status,
				COALESCE(document.payment_status, 'Unpaid') AS payment_status,
				COALESCE(document.payment_mode, '') AS payment_mode,
				COALESCE(document.customer_payment_ref, '') AS customer_payment_ref
			FROM `tabRoute Stop Document` document
			WHERE document.parent = %(route_name)s
				AND document.parenttype = 'Transport Route'
				AND document.parentfield = 'stop_documents'
			ORDER BY document.idx ASC
			""",
			{"route_name": route_name},
			as_dict=True,
		)
		order_items = frappe.db.sql(
			"""
			SELECT
				document.name AS route_document,
				item.parent AS sales_order,
				item.item_code,
				item.item_name,
				item.qty,
				item.uom,
				item.stock_uom
			FROM `tabRoute Stop Document` document
			INNER JOIN `tabSales Order Item` item
				ON document.ref_type = 'Sales Order'
				AND item.parent = document.ref_name
			WHERE document.parent = %(route_name)s
				AND document.parenttype = 'Transport Route'
				AND document.parentfield = 'stop_documents'
			ORDER BY document.idx ASC, item.idx ASC
			""",
			{"route_name": route_name},
			as_dict=True,
		)

		items_by_document: dict[str, list[dict]] = {}
		for item in order_items:
			item["qty"] = float(item.get("qty") or 0)
			items_by_document.setdefault(str(item.get("route_document") or ""), []).append(item)
		documents_by_stop: dict[str, list[dict]] = {}
		for document in documents:
			document["amount_due"] = float(document.get("amount_due") or 0)
			document["outstanding_amount"] = float(document.get("outstanding_amount") or 0)
			document["items"] = items_by_document.get(str(document.get("name") or ""), [])
			documents_by_stop.setdefault(str(document.get("stop") or ""), []).append(document)
		for stop in stops:
			stop["sequence"] = int(stop.get("sequence") or 0)
			stop["doc_count"] = int(stop.get("doc_count") or 0)
			stop["planned_amount_due"] = float(stop.get("planned_amount_due") or 0)
			stop["documents"] = documents_by_stop.get(str(stop.get("name") or ""), [])

		return {**headers[0], "stops": stops}

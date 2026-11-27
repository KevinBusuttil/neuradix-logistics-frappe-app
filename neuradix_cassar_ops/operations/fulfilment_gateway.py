from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from typing import Any

import frappe
from frappe.utils import flt, now_datetime, nowdate

from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note, make_sales_invoice

from neuradix_cassar_ops.operations.exceptions import OperationsValidationError
from neuradix_cassar_ops.operations.insight_gateway import InsightTransportGateway


TERMINAL_STOP_STATUSES = frozenset({"Delivered", "Partially Delivered", "Failed"})


class ERPNextFulfilmentGateway:
	"""Own the ERPNext document lifecycle around Insight transport routes."""

	def __init__(self, insight_gateway: InsightTransportGateway | None = None) -> None:
		self.insight_gateway = insight_gateway or InsightTransportGateway()

	def claim_check(self, *, pick_list_name: str, checker: str) -> dict[str, Any]:
		pick_list = self._draft_completed_pick_list(pick_list_name)
		assigned_checker = str(getattr(pick_list, "custom_checker", "") or "")
		if assigned_checker and assigned_checker != checker:
			raise OperationsValidationError("This Pick List is assigned to another checker.")
		self._assert_independent_checker(pick_list, checker)
		pick_list.custom_checker = checker
		pick_list.custom_checking_status = "In Progress"
		pick_list.save(ignore_permissions=True)
		return self._pick_workflow_result(pick_list)

	def finalize_pick(self, *, pick_list_name: str) -> dict[str, Any]:
		pick_list = frappe.get_doc("Pick List", pick_list_name)
		if pick_list.docstatus != 0:
			raise OperationsValidationError("Only a draft Pick List can be finalized.")
		for row in pick_list.locations:
			required_qty = flt(row.stock_qty or row.qty)
			if flt(row.picked_qty) + 1e-9 < required_qty:
				raise OperationsValidationError(
					f"Pick List row {row.idx} is incomplete: {row.picked_qty} of {required_qty} picked."
				)
		result = self.insight_gateway.finalize_pick_list(pick_list_name)
		pick_list.reload()
		pick_list.custom_checker = None
		pick_list.custom_checking_status = "Pending"
		pick_list.custom_checked_at = None
		pick_list.custom_check_notes = None
		pick_list.save(ignore_permissions=True)
		self._update_order_statuses_for_pick_list(pick_list, "Picked")
		return {**result, **self._pick_workflow_result(pick_list)}

	def approve_check(
		self,
		*,
		pick_list_name: str,
		checker: str,
		notes: str = "",
	) -> dict[str, Any]:
		pick_list = self._draft_completed_pick_list(pick_list_name)
		self._assert_independent_checker(pick_list, checker)
		assigned_checker = str(getattr(pick_list, "custom_checker", "") or "")
		if assigned_checker and assigned_checker != checker:
			raise OperationsValidationError("This Pick List is assigned to another checker.")
		for row in pick_list.locations:
			required_qty = flt(row.stock_qty or row.qty)
			if flt(row.picked_qty) + 1e-9 < required_qty:
				raise OperationsValidationError(
					f"Pick List row {row.idx} is incomplete: {row.picked_qty} of {required_qty} picked."
				)
		pick_list.custom_checker = checker
		pick_list.custom_checking_status = "Approved"
		pick_list.custom_checked_at = now_datetime()
		pick_list.custom_check_notes = notes[:2000]
		pick_list.save(ignore_permissions=True)
		pick_list.submit()
		self._update_order_statuses_for_pick_list(pick_list, "Checked")
		self._mark_route_picked_if_ready(str(pick_list.custom_transport_route or ""))
		return self._pick_workflow_result(pick_list)

	def reject_check(
		self,
		*,
		pick_list_name: str,
		checker: str,
		notes: str,
	) -> dict[str, Any]:
		pick_list = self._draft_completed_pick_list(pick_list_name)
		self._assert_independent_checker(pick_list, checker)
		if not notes.strip():
			raise OperationsValidationError("A rejection reason is required.")
		pick_list.custom_checker = checker
		pick_list.custom_checking_status = "Rejected"
		pick_list.custom_checked_at = now_datetime()
		pick_list.custom_check_notes = notes[:2000]
		pick_list.custom_picking_status = "In Progress"
		pick_list.save(ignore_permissions=True)
		self._update_order_statuses_for_pick_list(pick_list, "Picking", notes[:2000])
		return self._pick_workflow_result(pick_list)

	def prepare_load_manifest(self, *, route_name: str) -> dict[str, Any]:
		route = frappe.get_doc("Transport Route", route_name)
		pick_rows = self._route_pick_rows(route_name)
		if not pick_rows:
			raise OperationsValidationError("The route has no Pick Lists.")
		for row in pick_rows:
			if int(row.docstatus or 0) != 1 or row.checking_status != "Approved":
				raise OperationsValidationError(
					f"Pick List {row.name} must be independently approved before loading."
				)

		manifest_rows = frappe.db.sql(
			"""
			SELECT
				location.item_code,
				MAX(location.item_name) AS item_name,
				location.sales_order,
				document.stop,
				location.warehouse,
				MAX(location.uom) AS uom,
				MAX(location.stock_uom) AS stock_uom,
				SUM(location.picked_qty) AS qty
			FROM `tabPick List` pick_list
			INNER JOIN `tabPick List Item` location ON location.parent = pick_list.name
			INNER JOIN `tabRoute Stop Document` document
				ON document.parent = %(route_name)s
				AND document.parenttype = 'Transport Route'
				AND document.parentfield = 'stop_documents'
				AND document.ref_type = 'Sales Order'
				AND document.ref_name = location.sales_order
			WHERE pick_list.docstatus = 1
				AND pick_list.custom_transport_route = %(route_name)s
			GROUP BY
				location.item_code,
				location.sales_order,
				document.stop,
				location.warehouse
			ORDER BY document.stop, location.sales_order, location.item_code, location.warehouse
			""",
			{"route_name": route_name},
			as_dict=True,
		)
		if not manifest_rows:
			raise OperationsValidationError("No checked Pick List rows could be mapped to route stops.")

		existing_qty = {
			(self._load_key(row)): flt(row.picked_qty)
			for row in route.load_lines
		}
		route.set("load_lines", [])
		for row in manifest_rows:
			qty = flt(row.qty)
			loaded_qty = min(qty, existing_qty.get(self._load_key(row), 0.0))
			route.append(
				"load_lines",
				{
					"item_code": row.item_code,
					"item_name": row.item_name,
					"qty": qty,
					"picked_qty": loaded_qty,
					"uom": row.uom,
					"stock_uom": row.stock_uom,
					"sales_order": row.sales_order,
					"stop": row.stop,
					"warehouse": row.warehouse,
					"status": self._quantity_status(loaded_qty, qty),
				},
			)
		route.status = "Picked"
		route.save(ignore_permissions=True)
		self._update_route_order_statuses(route.name, "Checked")
		return self._route_result(route)

	def begin_loading(self, *, route_name: str) -> dict[str, Any]:
		route = frappe.get_doc("Transport Route", route_name)
		if route.status not in {"Picked", "Loading"}:
			raise OperationsValidationError("Only a checked route can start loading.")
		if not route.load_lines:
			self.prepare_load_manifest(route_name=route_name)
			route.reload()
		route.status = "Loading"
		route.save(ignore_permissions=True)
		self._update_route_order_statuses(route.name, "Loading")
		return self._route_result(route)

	def record_load_scan(
		self,
		*,
		route_name: str,
		scanned_code: str,
		qty: float,
	) -> dict[str, Any]:
		route = frappe.get_doc("Transport Route", route_name)
		if route.status != "Loading":
			raise OperationsValidationError("The route must be in Loading status before scanning.")
		item_code = self._resolve_item_code(scanned_code)
		remaining = flt(qty)
		if remaining <= 0:
			raise OperationsValidationError("qty must be a positive number.")
		updated_lines: list[str] = []
		for line in route.load_lines:
			if line.item_code != item_code:
				continue
			available = max(0.0, flt(line.qty) - flt(line.picked_qty))
			if available <= 0:
				continue
			increment = min(available, remaining)
			line.picked_qty = flt(line.picked_qty) + increment
			line.status = self._quantity_status(line.picked_qty, line.qty)
			updated_lines.append(str(line.name or line.idx))
			remaining -= increment
			if remaining <= 1e-9:
				break
		if remaining > 1e-9:
			raise OperationsValidationError(
				f"Scan exceeds the remaining load quantity for item {item_code}."
			)
		route.save(ignore_permissions=True)
		return {
			**self._route_result(route),
			"item_code": item_code,
			"updated_lines": updated_lines,
		}

	def complete_loading(self, *, route_name: str) -> dict[str, Any]:
		route = frappe.get_doc("Transport Route", route_name)
		if route.status not in {"Loading", "Loaded"}:
			raise OperationsValidationError("The route is not being loaded.")
		incomplete = [
			line for line in route.load_lines if flt(line.picked_qty) + 1e-9 < flt(line.qty)
		]
		if incomplete:
			raise OperationsValidationError(
				f"{len(incomplete)} route load line(s) are incomplete."
			)
		route.status = "Loaded"
		for line in route.load_lines:
			line.status = "Picked"
		route.save(ignore_permissions=True)
		self._update_route_order_statuses(route.name, "Loaded")
		return self._route_result(route)

	def dispatch_route(self, *, route_name: str, event_id: str) -> dict[str, Any]:
		route = frappe.get_doc("Transport Route", route_name)
		if route.status == "Dispatched":
			return self._existing_dispatch_result(route)
		if route.status != "Loaded":
			raise OperationsValidationError("A route must be fully loaded before dispatch.")
		if not route.vehicle or not route.driver:
			raise OperationsValidationError("Vehicle and driver are required before dispatch.")
		if not route.load_lines:
			raise OperationsValidationError("The route has no load manifest.")

		plan = frappe.get_doc("Transport Plan", route.transport_plan)
		transit_warehouse = self._get_transit_warehouse(route.vehicle)
		stock_entry = self._create_stock_transfer(
			route=route,
			company=plan.company,
			target_warehouse=transit_warehouse,
			event_id=event_id,
		)
		invoices: list[str] = []
		delivery_notes: list[str] = []
		for sales_order, stop_name in self._route_orders(route.name):
			invoice = self._create_dispatch_invoice(
				sales_order=sales_order,
				route_name=route.name,
				stop_name=stop_name,
				event_id=event_id,
			)
			delivery_note = self._create_draft_delivery_note(
				sales_order=sales_order,
				route_name=route.name,
				stop_name=stop_name,
				transit_warehouse=transit_warehouse,
				event_id=event_id,
			)
			invoices.append(invoice.name)
			delivery_notes.append(delivery_note.name)
			self._append_route_document(route, stop_name, invoice)
			self._append_route_document(route, stop_name, delivery_note)

		route.status = "Dispatched"
		route.save(ignore_permissions=True)
		self._update_route_order_statuses(route.name, "Dispatched")
		plan.reload()
		plan.status = "In Progress"
		plan.save(ignore_permissions=True)
		return {
			**self._route_result(route),
			"transit_warehouse": transit_warehouse,
			"stock_entry": stock_entry.name,
			"sales_invoices": invoices,
			"delivery_notes": delivery_notes,
		}

	def complete_delivery(
		self,
		*,
		route_name: str,
		stop_name: str,
		delivery_result: str,
		delivered_quantities: list[dict[str, Any]],
		failure_reason: str,
		notes: str,
		collection_amount: float,
		collection_mode: str,
		collection_reference: str,
		event_id: str,
	) -> dict[str, Any]:
		route = frappe.get_doc("Transport Route", route_name)
		if route.status not in {"Dispatched", "Invoiced"}:
			raise OperationsValidationError("The route has not been dispatched.")
		stop = next((row for row in route.stops if row.name == stop_name), None)
		if stop is None:
			raise OperationsValidationError(
				f"Route stop {stop_name} does not belong to Transport Route {route_name}."
			)
		if stop.status in TERMINAL_STOP_STATUSES:
			raise OperationsValidationError("This route stop already has a delivery outcome.")
		if delivery_result == "Failed" and not failure_reason.strip():
			raise OperationsValidationError("A failure reason is required for a failed delivery.")

		quantity_map = self._delivered_quantity_map(delivered_quantities)
		delivery_notes = self._draft_delivery_notes(route_name, stop_name)
		if not delivery_notes:
			raise OperationsValidationError("No draft Delivery Notes were prepared for this stop.")
		available_quantities: dict[tuple[str, str], float] = defaultdict(float)
		for delivery_note in delivery_notes:
			for row in delivery_note.items:
				available_quantities[
					(str(row.against_sales_order or ""), str(row.item_code))
				] += flt(row.qty)
		delivered_totals = self._validated_delivery_totals(
			delivery_result=delivery_result,
			requested=quantity_map,
			available=available_quantities,
		)
		remaining_delivered = defaultdict(float, delivered_totals)
		invoices = self._submitted_invoices(route_name, stop_name)
		undelivered: dict[tuple[str, str, str], float] = defaultdict(float)
		submitted_delivery_notes: list[str] = []
		for delivery_note in delivery_notes:
			original_rows = [
				{
					"sales_order": str(row.against_sales_order or ""),
					"item_code": str(row.item_code),
					"warehouse": str(row.warehouse),
					"qty": flt(row.qty),
				}
				for row in delivery_note.items
			]
			kept_rows = []
			for row, original in zip(list(delivery_note.items), original_rows, strict=True):
				key = (original["sales_order"], original["item_code"])
				delivered_qty = min(original["qty"], remaining_delivered[key])
				remaining_delivered[key] -= delivered_qty
				missing_qty = max(0.0, original["qty"] - delivered_qty)
				if missing_qty:
					undelivered[(original["sales_order"], original["item_code"], original["warehouse"])] += missing_qty
				if delivered_qty:
					row.qty = delivered_qty
					kept_rows.append(row)

			if kept_rows:
				delivery_note.set("items", kept_rows)
				delivery_note.custom_ops_event_id = event_id
				delivery_note.run_method("calculate_taxes_and_totals")
				delivery_note.save(ignore_permissions=True)
				delivery_note.submit()
				submitted_delivery_notes.append(delivery_note.name)
			else:
				frappe.delete_doc("Delivery Note", delivery_note.name, ignore_permissions=True, force=True)
				self._unlink_route_document(
					route_name=route.name,
					ref_type="Delivery Note",
					ref_name=delivery_note.name,
				)

		return_stock_entry = self._return_undelivered_stock(
			route=route,
			undelivered=undelivered,
			event_id=event_id,
			stop_name=stop_name,
		)
		credit_notes = self._credit_undelivered_invoices(
			invoices=invoices,
			undelivered=undelivered,
			route_name=route.name,
			stop_name=stop_name,
			event_id=event_id,
		)
		payment_entries = self._record_collection(
			route=route,
			stop=stop,
			invoices=invoices,
			amount=flt(collection_amount),
			mode=collection_mode,
			reference=collection_reference,
			event_id=event_id,
		)

		outcome = self.insight_gateway.record_route_stop_outcome(
			route_name=route.name,
			stop_name=stop.name,
			delivery_result=delivery_result,
			failure_reason=failure_reason,
			delivered_notes=notes,
			payment_entry=payment_entries[0] if payment_entries else "",
			payment_mode=collection_mode if payment_entries else "",
		)
		order_status = {
			"Delivered": "Delivered",
			"Partially Delivered": "Backordered",
			"Failed": "Delivery Failed",
		}[delivery_result]
		self._update_stop_order_statuses(
			route.name,
			stop.name,
			order_status,
			failure_reason or notes,
		)
		reconciliation = self.reconcile_route(route_name=route.name)
		return {
			**outcome,
			"route_status": reconciliation["route_status"],
			"delivery_notes": submitted_delivery_notes,
			"sales_invoices": [invoice.name for invoice in invoices],
			"return_stock_entry": return_stock_entry.name if return_stock_entry else "",
			"credit_notes": [credit_note.name for credit_note in credit_notes],
			"payment_entries": payment_entries,
		}

	def delivery_requirements(self) -> dict[str, bool]:
		return {
			"require_signature": self._setting_enabled("require_signature", default=True),
			"require_gps": self._setting_enabled("require_gps", default=False),
			"enable_driver_collections": self._setting_enabled(
				"enable_driver_collections",
				default=True,
			),
		}

	@staticmethod
	def operations_defaults() -> dict[str, str]:
		rows = frappe.db.sql(
			"""
			SELECT field, value
			FROM `tabSingles`
			WHERE doctype = 'Cassar Operations Settings'
				AND field IN (
					'default_company',
					'default_source_warehouse',
					'default_collection_mode'
				)
			""",
			as_dict=True,
		)
		values = {str(row.field): str(row.value or "") for row in rows}
		return {
			"company": values.get("default_company", ""),
			"source_warehouse": values.get("default_source_warehouse", ""),
			"collection_mode": values.get("default_collection_mode", ""),
		}

	def reconcile_route(self, *, route_name: str) -> dict[str, Any]:
		route = frappe.get_doc("Transport Route", route_name)
		if route.stops and all(stop.status in TERMINAL_STOP_STATUSES for stop in route.stops):
			route.status = "Closed"
			route.save(ignore_permissions=True)
			plan_routes = frappe.get_all(
				"Transport Route",
				filters={"transport_plan": route.transport_plan, "docstatus": ["<", 2]},
				fields=["status"],
			)
			if plan_routes and all(row.status == "Closed" for row in plan_routes):
				plan = frappe.get_doc("Transport Plan", route.transport_plan)
				plan.status = "Completed"
				plan.save(ignore_permissions=True)
		return {"route_name": route.name, "route_status": route.status}

	def _draft_completed_pick_list(self, pick_list_name: str):
		pick_list = frappe.get_doc("Pick List", pick_list_name)
		if pick_list.docstatus != 0:
			raise OperationsValidationError("Only a draft Pick List can be checked.")
		if str(getattr(pick_list, "custom_picking_status", "") or "") != "Done":
			raise OperationsValidationError("The picker must complete the Pick List before checking.")
		return pick_list

	def _assert_independent_checker(self, pick_list, checker: str) -> None:
		if not self._setting_enabled("checker_must_differ_from_picker", default=True):
			return
		if str(getattr(pick_list, "custom_picker", "") or "") == checker:
			raise OperationsValidationError("The checker must be different from the picker.")

	def _route_pick_rows(self, route_name: str) -> list[Any]:
		return frappe.db.sql(
			"""
			SELECT
				name,
				docstatus,
				COALESCE(custom_checking_status, 'Pending') AS checking_status
			FROM `tabPick List`
			WHERE docstatus < 2 AND custom_transport_route = %(route_name)s
			ORDER BY creation ASC
			""",
			{"route_name": route_name},
			as_dict=True,
		)

	def _mark_route_picked_if_ready(self, route_name: str) -> None:
		if not route_name:
			return
		rows = self._route_pick_rows(route_name)
		if not rows or not all(
			int(row.docstatus or 0) == 1 and row.checking_status == "Approved" for row in rows
		):
			return
		route = frappe.get_doc("Transport Route", route_name)
		if route.status in {"Not started", "Created"}:
			route.status = "Picked"
			route.save(ignore_permissions=True)

	def _update_order_statuses_for_pick_list(
		self,
		pick_list,
		status: str,
		hold_reason: str = "",
	) -> None:
		orders = {str(row.sales_order) for row in pick_list.locations if row.sales_order}
		for order in orders:
			frappe.db.set_value(
				"Sales Order",
				order,
				{
					"custom_ops_status": status,
					"custom_ops_hold_reason": hold_reason,
					"custom_transport_plan": pick_list.custom_transport_plan,
					"custom_transport_route": pick_list.custom_transport_route,
				},
				update_modified=True,
			)

	def _update_route_order_statuses(
		self,
		route_name: str,
		status: str,
		hold_reason: str = "",
	) -> None:
		for sales_order, _stop in self._route_orders(route_name):
			frappe.db.set_value(
				"Sales Order",
				sales_order,
				{
					"custom_ops_status": status,
					"custom_ops_hold_reason": hold_reason,
					"custom_transport_route": route_name,
				},
				update_modified=True,
			)

	def _update_stop_order_statuses(
		self,
		route_name: str,
		stop_name: str,
		status: str,
		hold_reason: str,
	) -> None:
		for sales_order, row_stop in self._route_orders(route_name):
			if row_stop != stop_name:
				continue
			frappe.db.set_value(
				"Sales Order",
				sales_order,
				{
					"custom_ops_status": status,
					"custom_ops_hold_reason": hold_reason,
				},
				update_modified=True,
			)

	def _get_transit_warehouse(self, vehicle: str) -> str:
		warehouse = frappe.db.get_value("Vehicle", vehicle, "custom_transit_warehouse")
		if not warehouse:
			warehouse = frappe.db.get_single_value(
				"Cassar Operations Settings",
				"default_vehicle_transit_warehouse",
			)
		if not warehouse:
			raise OperationsValidationError(
				"Configure a Transit Warehouse on the Vehicle or Cassar Operations Settings."
			)
		if frappe.db.get_value("Warehouse", warehouse, "is_group"):
			raise OperationsValidationError("The vehicle transit warehouse cannot be a group warehouse.")
		return str(warehouse)

	def _create_stock_transfer(self, *, route, company: str, target_warehouse: str, event_id: str):
		existing = frappe.db.get_value(
			"Stock Entry",
			{
				"custom_transport_route": route.name,
				"custom_ops_event_id": event_id,
				"docstatus": 1,
			},
			"name",
		)
		if existing:
			return frappe.get_doc("Stock Entry", existing)

		grouped: dict[tuple[str, str, str], float] = defaultdict(float)
		for line in route.load_lines:
			grouped[(str(line.item_code), str(line.warehouse), str(line.stock_uom or line.uom))] += flt(
				line.qty
			)
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Transfer"
		stock_entry.company = company
		stock_entry.posting_date = nowdate()
		stock_entry.custom_transport_route = route.name
		stock_entry.custom_ops_event_id = event_id
		for (item_code, source_warehouse, stock_uom), qty in grouped.items():
			stock_entry.append(
				"items",
				{
					"item_code": item_code,
					"qty": qty,
					"uom": stock_uom,
					"stock_uom": stock_uom,
					"conversion_factor": 1,
					"s_warehouse": source_warehouse,
					"t_warehouse": target_warehouse,
				},
			)
		stock_entry.set_missing_values()
		stock_entry.insert(ignore_permissions=True)
		stock_entry.submit()
		return stock_entry

	def _create_dispatch_invoice(
		self,
		*,
		sales_order: str,
		route_name: str,
		stop_name: str,
		event_id: str,
	):
		existing = self._linked_document(
			parent_doctype="Sales Invoice",
			child_table="Sales Invoice Item",
			child_sales_order_field="sales_order",
			sales_order=sales_order,
			route_name=route_name,
			docstatus=(1,),
		)
		if existing:
			return frappe.get_doc("Sales Invoice", existing)
		invoice = make_sales_invoice(sales_order, ignore_permissions=True)
		invoice.update_stock = 0
		invoice.custom_transport_route = route_name
		invoice.custom_route_stop = stop_name
		invoice.custom_ops_event_id = event_id
		invoice.insert(ignore_permissions=True)
		invoice.submit()
		return invoice

	def _create_draft_delivery_note(
		self,
		*,
		sales_order: str,
		route_name: str,
		stop_name: str,
		transit_warehouse: str,
		event_id: str,
	):
		existing = self._linked_document(
			parent_doctype="Delivery Note",
			child_table="Delivery Note Item",
			child_sales_order_field="against_sales_order",
			sales_order=sales_order,
			route_name=route_name,
			docstatus=(0, 1),
		)
		if existing:
			return frappe.get_doc("Delivery Note", existing)
		delivery_note = make_delivery_note(
			sales_order,
			kwargs={"ignore_pricing_rule": True, "skip_item_mapping": False},
		)
		if not delivery_note or not delivery_note.items:
			raise OperationsValidationError(
				f"Sales Order {sales_order} has no remaining quantity for delivery."
			)
		delivery_note.custom_transport_route = route_name
		delivery_note.custom_route_stop = stop_name
		delivery_note.custom_ops_event_id = event_id
		for row in delivery_note.items:
			row.warehouse = transit_warehouse
		delivery_note.set_missing_values()
		delivery_note.insert(ignore_permissions=True)
		return delivery_note

	def _linked_document(
		self,
		*,
		parent_doctype: str,
		child_table: str,
		child_sales_order_field: str,
		sales_order: str,
		route_name: str,
		docstatus: tuple[int, ...],
	) -> str | None:
		rows = frappe.db.sql(
			f"""
			SELECT DISTINCT parent.name
			FROM `tab{parent_doctype}` parent
			INNER JOIN `tab{child_table}` item ON item.parent = parent.name
			WHERE parent.docstatus IN %(docstatus)s
				AND parent.custom_transport_route = %(route_name)s
				AND item.{child_sales_order_field} = %(sales_order)s
			ORDER BY parent.creation ASC
			LIMIT 1
			""",
			{
				"docstatus": docstatus,
				"route_name": route_name,
				"sales_order": sales_order,
			},
			as_dict=True,
		)
		return str(rows[0].name) if rows else None

	def _append_route_document(self, route, stop_name: str, document) -> None:
		if any(
			row.ref_type == document.doctype and row.ref_name == document.name
			for row in route.stop_documents
		):
			return
		route.append(
			"stop_documents",
			{
				"stop": stop_name,
				"ref_type": document.doctype,
				"ref_name": document.name,
				"customer": document.customer,
				"amount_due": flt(document.grand_total),
				"outstanding_amount": flt(getattr(document, "outstanding_amount", document.grand_total)),
				"delivery_status": "Undelivered",
				"payment_status": "Unpaid",
			},
		)

	@staticmethod
	def _unlink_route_document(*, route_name: str, ref_type: str, ref_name: str) -> None:
		"""Remove a route child link before deleting an unfulfilled draft document."""
		frappe.db.sql(
			"""
			DELETE FROM `tabRoute Stop Document`
			WHERE parent = %(route_name)s
				AND parenttype = 'Transport Route'
				AND parentfield = 'stop_documents'
				AND ref_type = %(ref_type)s
				AND ref_name = %(ref_name)s
			""",
			{
				"route_name": route_name,
				"ref_type": ref_type,
				"ref_name": ref_name,
			},
		)

	def _existing_dispatch_result(self, route) -> dict[str, Any]:
		stock_entry = frappe.db.get_value(
			"Stock Entry",
			{"custom_transport_route": route.name, "docstatus": 1},
			"name",
		)
		return {
			**self._route_result(route),
			"stock_entry": stock_entry or "",
			"sales_invoices": [
				row.ref_name for row in route.stop_documents if row.ref_type == "Sales Invoice"
			],
			"delivery_notes": [
				row.ref_name for row in route.stop_documents if row.ref_type == "Delivery Note"
			],
		}

	def _draft_delivery_notes(self, route_name: str, stop_name: str) -> list[Any]:
		names = frappe.get_all(
			"Delivery Note",
			filters={
				"custom_transport_route": route_name,
				"custom_route_stop": stop_name,
				"docstatus": 0,
			},
			order_by="creation asc",
			pluck="name",
		)
		return [frappe.get_doc("Delivery Note", name) for name in names]

	def _submitted_invoices(self, route_name: str, stop_name: str) -> list[Any]:
		names = frappe.get_all(
			"Sales Invoice",
			filters={
				"custom_transport_route": route_name,
				"custom_route_stop": stop_name,
				"docstatus": 1,
				"is_return": 0,
			},
			order_by="creation asc",
			pluck="name",
		)
		return [frappe.get_doc("Sales Invoice", name) for name in names]

	def _return_undelivered_stock(
		self,
		*,
		route,
		undelivered: dict[tuple[str, str, str], float],
		event_id: str,
		stop_name: str,
	):
		if not undelivered:
			return None
		return_warehouse = frappe.db.get_single_value(
			"Cassar Operations Settings",
			"default_return_warehouse",
		)
		if not return_warehouse:
			raise OperationsValidationError("Configure the Default Return Warehouse before delivery.")
		company = frappe.db.get_value("Transport Plan", route.transport_plan, "company")
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Transfer"
		stock_entry.company = company
		stock_entry.posting_date = nowdate()
		stock_entry.custom_transport_route = route.name
		stock_entry.custom_route_stop = stop_name
		stock_entry.custom_ops_event_id = event_id
		for (_sales_order, item_code, transit_warehouse), qty in sorted(undelivered.items()):
			stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
			stock_entry.append(
				"items",
				{
					"item_code": item_code,
					"qty": qty,
					"uom": stock_uom,
					"stock_uom": stock_uom,
					"conversion_factor": 1,
					"s_warehouse": transit_warehouse,
					"t_warehouse": return_warehouse,
				},
			)
		stock_entry.set_missing_values()
		stock_entry.insert(ignore_permissions=True)
		stock_entry.submit()
		return stock_entry

	def _credit_undelivered_invoices(
		self,
		*,
		invoices: list[Any],
		undelivered: dict[tuple[str, str, str], float],
		route_name: str,
		stop_name: str,
		event_id: str,
	) -> list[Any]:
		if not undelivered:
			return []

		missing_by_order_item: dict[tuple[str, str], float] = defaultdict(float)
		for (sales_order, item_code, _warehouse), qty in undelivered.items():
			missing_by_order_item[(sales_order, item_code)] += qty
		credit_notes = []
		for invoice in invoices:
			with self._system_user():
				credit_note = make_sales_return(invoice.name)
			kept_rows = []
			for row in credit_note.items:
				key = (str(row.sales_order or ""), str(row.item_code))
				missing_qty = missing_by_order_item.get(key, 0.0)
				if missing_qty <= 0:
					continue
				return_qty = min(abs(flt(row.qty)), missing_qty)
				row.qty = -return_qty
				missing_by_order_item[key] -= return_qty
				kept_rows.append(row)
			if not kept_rows:
				continue
			credit_note.set("items", kept_rows)
			credit_note.update_stock = 0
			credit_note.custom_transport_route = route_name
			credit_note.custom_route_stop = stop_name
			credit_note.custom_ops_event_id = event_id
			credit_note.run_method("calculate_taxes_and_totals")
			credit_note.insert(ignore_permissions=True)
			credit_note.submit()
			credit_notes.append(credit_note)
		return credit_notes

	@staticmethod
	@contextmanager
	def _system_user():
		"""Allow ERPNext document mappers to run behind the role-checked API facade."""
		previous_user = frappe.session.user
		try:
			frappe.set_user("Administrator")
			yield
		finally:
			frappe.set_user(previous_user)

	def _record_collection(
		self,
		*,
		route,
		stop,
		invoices: list[Any],
		amount: float,
		mode: str,
		reference: str,
		event_id: str,
	) -> list[str]:
		if amount <= 0:
			return []
		if not self._setting_enabled("enable_driver_collections", default=True):
			raise OperationsValidationError("Driver collections are disabled.")
		mode = mode or str(
			frappe.db.get_single_value("Cassar Operations Settings", "default_collection_mode") or ""
		)
		if not mode:
			raise OperationsValidationError("A collection mode is required.")
		remaining = amount
		payment_entries: list[str] = []
		allocations: list[tuple[str, float]] = []
		for invoice in invoices:
			invoice.reload()
			outstanding = max(0.0, flt(invoice.outstanding_amount))
			if outstanding <= 0 or remaining <= 0:
				continue
			allocated = min(outstanding, remaining)
			bank_account = frappe.db.get_value(
				"Mode of Payment Account",
				{"parent": mode, "company": invoice.company},
				"default_account",
			)
			payment_entry = get_payment_entry(
				"Sales Invoice",
				invoice.name,
				party_amount=allocated,
				bank_account=bank_account,
			)
			payment_entry.mode_of_payment = mode
			payment_entry.reference_no = reference or event_id
			payment_entry.reference_date = nowdate()
			payment_entry.custom_transport_route = route.name
			payment_entry.custom_route_stop = stop.name
			payment_entry.custom_ops_event_id = event_id
			payment_entry.insert(ignore_permissions=True)
			payment_entry.submit()
			payment_entries.append(payment_entry.name)
			allocations.append((payment_entry.name, allocated))
			remaining -= allocated
		if remaining > 0.005:
			raise OperationsValidationError("Collection amount exceeds the stop's outstanding invoices.")
		for payment_entry, allocated in allocations:
			route.append(
				"collections",
				{
					"company": frappe.db.get_value("Transport Plan", route.transport_plan, "company"),
					"route": route.name,
					"stop": stop.name,
					"method": mode,
					"amount": allocated,
					"reference_no": reference or payment_entry,
					"collected_at": now_datetime(),
				},
			)
		route.save(ignore_permissions=True)
		return payment_entries

	@staticmethod
	def _delivered_quantity_map(rows: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
		quantity_map: dict[tuple[str, str], float] = defaultdict(float)
		for row in rows:
			sales_order = str(row.get("sales_order") or "").strip()
			item_code = str(row.get("item_code") or "").strip()
			qty = flt(row.get("qty"))
			if not sales_order or not item_code or qty < 0:
				raise OperationsValidationError(
					"Each delivered quantity needs sales_order, item_code, and a non-negative qty."
				)
			quantity_map[(sales_order, item_code)] += qty
		return quantity_map

	@staticmethod
	def _validated_delivery_totals(
		*,
		delivery_result: str,
		requested: dict[tuple[str, str], float],
		available: dict[tuple[str, str], float],
	) -> dict[tuple[str, str], float]:
		if delivery_result == "Delivered":
			return dict(available)
		if delivery_result == "Failed":
			return {}

		for key, qty in requested.items():
			if key not in available and qty > 0:
				raise OperationsValidationError(
					f"No dispatched item {key[1]} exists for Sales Order {key[0]}."
				)
			if qty > available.get(key, 0.0) + 1e-9:
				raise OperationsValidationError(
					f"Delivered quantity for {key[1]} on {key[0]} exceeds the dispatched quantity."
				)
		total_available = sum(available.values())
		total_delivered = sum(requested.values())
		if total_delivered <= 0 or total_delivered + 1e-9 >= total_available:
			raise OperationsValidationError(
				"A partial delivery needs both delivered and undelivered quantities."
			)
		return dict(requested)

	@staticmethod
	def _resolve_item_code(scanned_code: str) -> str:
		item_code = frappe.db.get_value("Item Barcode", {"barcode": scanned_code}, "parent")
		if not item_code and frappe.db.exists("Item", scanned_code):
			item_code = scanned_code
		if not item_code:
			raise OperationsValidationError(f"No item matches scan {scanned_code}.")
		return str(item_code)

	@staticmethod
	def _route_orders(route_name: str) -> list[tuple[str, str]]:
		rows = frappe.db.sql(
			"""
			SELECT DISTINCT ref_name AS sales_order, stop
			FROM `tabRoute Stop Document`
			WHERE parent = %(route_name)s
				AND parenttype = 'Transport Route'
				AND parentfield = 'stop_documents'
				AND ref_type = 'Sales Order'
			ORDER BY idx ASC
			""",
			{"route_name": route_name},
			as_dict=True,
		)
		return [(str(row.sales_order), str(row.stop)) for row in rows]

	@staticmethod
	def _load_key(row) -> tuple[str, str, str, str]:
		return (
			str(row.item_code or ""),
			str(row.sales_order or ""),
			str(row.stop or ""),
			str(row.warehouse or ""),
		)

	@staticmethod
	def _quantity_status(current: float, required: float) -> str:
		if flt(current) <= 0:
			return "Pending"
		if flt(current) + 1e-9 < flt(required):
			return "Partially Picked"
		return "Picked"

	@staticmethod
	def _pick_workflow_result(pick_list) -> dict[str, Any]:
		return {
			"name": pick_list.name,
			"docstatus": int(pick_list.docstatus),
			"picking_status": str(pick_list.custom_picking_status or ""),
			"picker": str(pick_list.custom_picker or ""),
			"checking_status": str(pick_list.custom_checking_status or ""),
			"checker": str(pick_list.custom_checker or ""),
		}

	@staticmethod
	def _route_result(route) -> dict[str, Any]:
		return {
			"route_name": route.name,
			"route_status": route.status,
			"load_line_count": len(route.load_lines),
			"loaded_line_count": sum(
				flt(line.picked_qty) + 1e-9 >= flt(line.qty) for line in route.load_lines
			),
		}

	@staticmethod
	def _setting_enabled(fieldname: str, *, default: bool) -> bool:
		value = frappe.db.get_single_value("Cassar Operations Settings", fieldname)
		return default if value is None else bool(int(value))

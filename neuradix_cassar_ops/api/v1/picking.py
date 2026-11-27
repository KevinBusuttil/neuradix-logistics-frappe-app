from __future__ import annotations

import frappe

from neuradix_cassar_ops.operations.event_service import (
	FrappeFulfilmentEventStore,
	process_pick_scan,
)
from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway
from neuradix_cassar_ops.operations.insight_gateway import InsightTransportGateway
from neuradix_cassar_ops.operations.mobile_repository import SQLMobileOperationsRepository
from neuradix_cassar_ops.operations.mobile_services import (
	PICKING_ROLES,
	_require_any,
	get_pick_detail,
	list_pick_queue,
)
from neuradix_cassar_ops.operations.workflow_service import finalize_pick


def _roles() -> list[str]:
	return frappe.get_roles(frappe.session.user)


@frappe.whitelist()
def get_queue(queue: str = "available", status: str | None = None, limit: int = 100) -> dict:
	if queue not in {"available", "mine", "checking"}:
		frappe.throw("queue must be available, mine, or checking.", frappe.ValidationError)
	return list_pick_queue(
		SQLMobileOperationsRepository(),
		user=frappe.session.user,
		roles=_roles(),
		queue=queue,
		status=status,
		limit=int(limit),
	)


@frappe.whitelist()
def get_pick_list(pick_list_name: str) -> dict:
	return get_pick_detail(
		SQLMobileOperationsRepository(),
		user=frappe.session.user,
		roles=_roles(),
		pick_list_name=pick_list_name,
	)


@frappe.whitelist(methods=["POST"])
def claim_pick_list(pick_list_name: str) -> dict:
	_require_any(_roles(), PICKING_ROLES, "Warehouse picking access is required.")
	return {"name": InsightTransportGateway().claim_pick_list(pick_list_name)}


@frappe.whitelist(methods=["POST"])
def record_scan(
	event_id: str,
	pick_list_name: str,
	scanned_code: str,
	qty: float = 1,
	device_id: str = "",
	client_timestamp: str | None = None,
) -> dict:
	return process_pick_scan(
		event={
			"event_id": event_id,
			"pick_list_name": pick_list_name,
			"scanned_code": scanned_code,
			"qty": qty,
			"device_id": device_id,
			"client_timestamp": client_timestamp,
		},
		user=frappe.session.user,
		roles=_roles(),
		repository=SQLMobileOperationsRepository(),
		gateway=InsightTransportGateway(),
		event_store=FrappeFulfilmentEventStore(),
	)


@frappe.whitelist(methods=["POST"])
def finalize_pick_list(
	event_id: str,
	pick_list_name: str,
	device_id: str = "",
	client_timestamp: str | None = None,
) -> dict:
	return finalize_pick(
		event={
			"event_id": event_id,
			"pick_list_name": pick_list_name,
			"device_id": device_id,
			"client_timestamp": client_timestamp,
		},
		user=frappe.session.user,
		roles=_roles(),
		repository=SQLMobileOperationsRepository(),
		fulfilment_gateway=ERPNextFulfilmentGateway(),
		event_store=FrappeFulfilmentEventStore(),
	)

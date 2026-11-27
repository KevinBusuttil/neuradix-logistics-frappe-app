from __future__ import annotations

import frappe

from neuradix_cassar_ops.operations.delivery_service import (
	FrappeDeliveryEventStore,
	record_proof_of_delivery,
)
from neuradix_cassar_ops.operations.insight_gateway import InsightTransportGateway
from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway
from neuradix_cassar_ops.operations.mobile_repository import SQLMobileOperationsRepository
from neuradix_cassar_ops.operations.mobile_services import get_route_detail, list_route_queue


@frappe.whitelist()
def get_routes(delivery_date: str | None = None, limit: int = 100) -> dict:
	return list_route_queue(
		SQLMobileOperationsRepository(),
		user=frappe.session.user,
		roles=frappe.get_roles(frappe.session.user),
		delivery_date=delivery_date,
		limit=int(limit),
	)


@frappe.whitelist()
def get_route(route_name: str) -> dict:
	return get_route_detail(
		SQLMobileOperationsRepository(),
		user=frappe.session.user,
		roles=frappe.get_roles(frappe.session.user),
		route_name=route_name,
	)


@frappe.whitelist(methods=["POST"])
def submit_proof_of_delivery(
	event_id: str,
	route_name: str,
	stop_name: str,
	recipient_name: str,
	signature_base64: str,
	delivery_result: str = "Delivered",
	delivered_quantities_json: str = "[]",
	failure_reason: str = "",
	photo_base64: str = "",
	latitude: float | None = None,
	longitude: float | None = None,
	collection_amount: float = 0,
	collection_mode: str = "",
	collection_reference: str = "",
	notes: str = "",
	device_id: str = "",
	client_timestamp: str | None = None,
) -> dict:
	return record_proof_of_delivery(
		event={
			"event_id": event_id,
			"route_name": route_name,
			"stop_name": stop_name,
			"recipient_name": recipient_name,
			"signature_base64": signature_base64,
			"delivery_result": delivery_result,
			"delivered_quantities": delivered_quantities_json,
			"failure_reason": failure_reason,
			"photo_base64": photo_base64,
			"latitude": latitude,
			"longitude": longitude,
			"collection_amount": collection_amount,
			"collection_mode": collection_mode,
			"collection_reference": collection_reference,
			"notes": notes,
			"device_id": device_id,
			"client_timestamp": client_timestamp,
		},
		user=frappe.session.user,
		roles=frappe.get_roles(frappe.session.user),
		repository=SQLMobileOperationsRepository(),
		gateway=ERPNextFulfilmentGateway(InsightTransportGateway()),
		event_store=FrappeDeliveryEventStore(),
	)

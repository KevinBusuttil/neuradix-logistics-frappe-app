from __future__ import annotations

import frappe

from neuradix_cassar_ops.operations.event_service import FrappeFulfilmentEventStore
from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway
from neuradix_cassar_ops.operations.mobile_repository import SQLMobileOperationsRepository
from neuradix_cassar_ops.operations.workflow_service import (
	begin_loading,
	complete_loading,
	dispatch_route,
	prepare_load_manifest,
	record_load_scan,
)


def _context() -> tuple[str, list[str]]:
	user = frappe.session.user
	return user, frappe.get_roles(user)


def _event(
	*,
	event_id: str,
	route_name: str,
	device_id: str,
	client_timestamp: str | None,
	**values,
) -> dict:
	return {
		"event_id": event_id,
		"route_name": route_name,
		"device_id": device_id,
		"client_timestamp": client_timestamp,
		**values,
	}


@frappe.whitelist()
def get_manifest(route_name: str) -> dict:
	user, roles = _context()
	from neuradix_cassar_ops.operations.mobile_services import get_route_detail

	get_route_detail(
		SQLMobileOperationsRepository(),
		user=user,
		roles=roles,
		route_name=route_name,
	)
	return {"route": SQLMobileOperationsRepository().get_route_load_manifest(route_name=route_name)}


@frappe.whitelist(methods=["POST"])
def prepare_manifest(route_name: str) -> dict:
	user, roles = _context()
	return prepare_load_manifest(
		route_name=route_name,
		user=user,
		roles=roles,
		repository=SQLMobileOperationsRepository(),
		fulfilment_gateway=ERPNextFulfilmentGateway(),
	)


@frappe.whitelist(methods=["POST"])
def start(
	event_id: str,
	route_name: str,
	device_id: str = "",
	client_timestamp: str | None = None,
) -> dict:
	user, roles = _context()
	return begin_loading(
		event=_event(
			event_id=event_id,
			route_name=route_name,
			device_id=device_id,
			client_timestamp=client_timestamp,
		),
		user=user,
		roles=roles,
		repository=SQLMobileOperationsRepository(),
		fulfilment_gateway=ERPNextFulfilmentGateway(),
		event_store=FrappeFulfilmentEventStore(),
	)


@frappe.whitelist(methods=["POST"])
def scan(
	event_id: str,
	route_name: str,
	scanned_code: str,
	qty: float = 1,
	device_id: str = "",
	client_timestamp: str | None = None,
) -> dict:
	user, roles = _context()
	return record_load_scan(
		event=_event(
			event_id=event_id,
			route_name=route_name,
			device_id=device_id,
			client_timestamp=client_timestamp,
			scanned_code=scanned_code,
			qty=qty,
		),
		user=user,
		roles=roles,
		repository=SQLMobileOperationsRepository(),
		fulfilment_gateway=ERPNextFulfilmentGateway(),
		event_store=FrappeFulfilmentEventStore(),
	)


@frappe.whitelist(methods=["POST"])
def complete(
	event_id: str,
	route_name: str,
	device_id: str = "",
	client_timestamp: str | None = None,
) -> dict:
	user, roles = _context()
	return complete_loading(
		event=_event(
			event_id=event_id,
			route_name=route_name,
			device_id=device_id,
			client_timestamp=client_timestamp,
		),
		user=user,
		roles=roles,
		repository=SQLMobileOperationsRepository(),
		fulfilment_gateway=ERPNextFulfilmentGateway(),
		event_store=FrappeFulfilmentEventStore(),
	)


@frappe.whitelist(methods=["POST"])
def dispatch(
	event_id: str,
	route_name: str,
	device_id: str = "",
	client_timestamp: str | None = None,
) -> dict:
	user, roles = _context()
	return dispatch_route(
		event=_event(
			event_id=event_id,
			route_name=route_name,
			device_id=device_id,
			client_timestamp=client_timestamp,
		),
		user=user,
		roles=roles,
		repository=SQLMobileOperationsRepository(),
		fulfilment_gateway=ERPNextFulfilmentGateway(),
		event_store=FrappeFulfilmentEventStore(),
	)


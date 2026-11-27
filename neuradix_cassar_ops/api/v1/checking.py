from __future__ import annotations

import frappe

from neuradix_cassar_ops.operations.event_service import FrappeFulfilmentEventStore
from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway
from neuradix_cassar_ops.operations.workflow_service import approve_check, claim_check, reject_check


def _context() -> tuple[str, list[str]]:
	user = frappe.session.user
	return user, frappe.get_roles(user)


@frappe.whitelist(methods=["POST"])
def claim(
	event_id: str,
	pick_list_name: str,
	device_id: str = "",
	client_timestamp: str | None = None,
) -> dict:
	user, roles = _context()
	return claim_check(
		event={
			"event_id": event_id,
			"pick_list_name": pick_list_name,
			"device_id": device_id,
			"client_timestamp": client_timestamp,
		},
		user=user,
		roles=roles,
		fulfilment_gateway=ERPNextFulfilmentGateway(),
		event_store=FrappeFulfilmentEventStore(),
	)


@frappe.whitelist(methods=["POST"])
def approve(
	event_id: str,
	pick_list_name: str,
	notes: str = "",
	device_id: str = "",
	client_timestamp: str | None = None,
) -> dict:
	user, roles = _context()
	return approve_check(
		event={
			"event_id": event_id,
			"pick_list_name": pick_list_name,
			"notes": notes,
			"device_id": device_id,
			"client_timestamp": client_timestamp,
		},
		user=user,
		roles=roles,
		fulfilment_gateway=ERPNextFulfilmentGateway(),
		event_store=FrappeFulfilmentEventStore(),
	)


@frappe.whitelist(methods=["POST"])
def reject(
	event_id: str,
	pick_list_name: str,
	notes: str,
	device_id: str = "",
	client_timestamp: str | None = None,
) -> dict:
	user, roles = _context()
	return reject_check(
		event={
			"event_id": event_id,
			"pick_list_name": pick_list_name,
			"notes": notes,
			"device_id": device_id,
			"client_timestamp": client_timestamp,
		},
		user=user,
		roles=roles,
		fulfilment_gateway=ERPNextFulfilmentGateway(),
		event_store=FrappeFulfilmentEventStore(),
	)


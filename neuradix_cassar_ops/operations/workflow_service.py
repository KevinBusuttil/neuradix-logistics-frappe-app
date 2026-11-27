from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from neuradix_cassar_ops.operations.exceptions import OperationsValidationError
from neuradix_cassar_ops.operations.mobile_services import (
	CHECKING_ROLES,
	PICKING_ROLES,
	ROUTE_MANAGER_ROLES,
	_require_any,
	get_pick_detail,
	get_route_detail,
)


def finalize_pick(
	*,
	event: dict[str, Any],
	user: str,
	roles: Iterable[str],
	repository,
	fulfilment_gateway,
	event_store,
) -> dict[str, Any]:
	_require_any(roles, PICKING_ROLES, "Warehouse picking access is required.")
	pick_list_name = _required_text(event, "pick_list_name")
	get_pick_detail(
		repository,
		user=user,
		roles=roles,
		pick_list_name=pick_list_name,
	)
	return _run_once(
		event=event,
		event_type="Pick Finalized",
		reference_doctype="Pick List",
		reference_name=pick_list_name,
		user=user,
		event_store=event_store,
		action=lambda: fulfilment_gateway.finalize_pick(pick_list_name=pick_list_name),
	)


def claim_check(
	*,
	event: dict[str, Any],
	user: str,
	roles: Iterable[str],
	fulfilment_gateway,
	event_store,
) -> dict[str, Any]:
	_require_any(roles, CHECKING_ROLES, "Independent checking access is required.")
	pick_list_name = _required_text(event, "pick_list_name")
	return _run_once(
		event=event,
		event_type="Check Claimed",
		reference_doctype="Pick List",
		reference_name=pick_list_name,
		user=user,
		event_store=event_store,
		action=lambda: fulfilment_gateway.claim_check(
			pick_list_name=pick_list_name,
			checker=user,
		),
	)


def approve_check(
	*,
	event: dict[str, Any],
	user: str,
	roles: Iterable[str],
	fulfilment_gateway,
	event_store,
) -> dict[str, Any]:
	_require_any(roles, CHECKING_ROLES, "Independent checking access is required.")
	pick_list_name = _required_text(event, "pick_list_name")
	return _run_once(
		event=event,
		event_type="Check Approved",
		reference_doctype="Pick List",
		reference_name=pick_list_name,
		user=user,
		event_store=event_store,
		action=lambda: fulfilment_gateway.approve_check(
			pick_list_name=pick_list_name,
			checker=user,
			notes=str(event.get("notes") or ""),
		),
	)


def reject_check(
	*,
	event: dict[str, Any],
	user: str,
	roles: Iterable[str],
	fulfilment_gateway,
	event_store,
) -> dict[str, Any]:
	_require_any(roles, CHECKING_ROLES, "Independent checking access is required.")
	pick_list_name = _required_text(event, "pick_list_name")
	return _run_once(
		event=event,
		event_type="Check Rejected",
		reference_doctype="Pick List",
		reference_name=pick_list_name,
		user=user,
		event_store=event_store,
		action=lambda: fulfilment_gateway.reject_check(
			pick_list_name=pick_list_name,
			checker=user,
			notes=_required_text(event, "notes"),
		),
	)


def prepare_load_manifest(
	*,
	route_name: str,
	user: str,
	roles: Iterable[str],
	repository,
	fulfilment_gateway,
) -> dict[str, Any]:
	_require_any(roles, ROUTE_MANAGER_ROLES, "Route loading access is required.")
	get_route_detail(repository, user=user, roles=roles, route_name=route_name)
	return fulfilment_gateway.prepare_load_manifest(route_name=route_name)


def begin_loading(
	*,
	event: dict[str, Any],
	user: str,
	roles: Iterable[str],
	repository,
	fulfilment_gateway,
	event_store,
) -> dict[str, Any]:
	_require_any(roles, ROUTE_MANAGER_ROLES, "Route loading access is required.")
	route_name = _required_text(event, "route_name")
	get_route_detail(repository, user=user, roles=roles, route_name=route_name)
	return _run_once(
		event=event,
		event_type="Load Started",
		reference_doctype="Transport Route",
		reference_name=route_name,
		user=user,
		event_store=event_store,
		action=lambda: fulfilment_gateway.begin_loading(route_name=route_name),
	)


def record_load_scan(
	*,
	event: dict[str, Any],
	user: str,
	roles: Iterable[str],
	repository,
	fulfilment_gateway,
	event_store,
) -> dict[str, Any]:
	_require_any(roles, ROUTE_MANAGER_ROLES, "Route loading access is required.")
	route_name = _required_text(event, "route_name")
	scanned_code = _required_text(event, "scanned_code")
	try:
		qty = float(event.get("qty") or 0)
	except (TypeError, ValueError) as exception:
		raise OperationsValidationError("qty must be a positive number.") from exception
	if qty <= 0:
		raise OperationsValidationError("qty must be a positive number.")
	get_route_detail(repository, user=user, roles=roles, route_name=route_name)
	return _run_once(
		event=event,
		event_type="Load Scan",
		reference_doctype="Transport Route",
		reference_name=route_name,
		user=user,
		event_store=event_store,
		action=lambda: fulfilment_gateway.record_load_scan(
			route_name=route_name,
			scanned_code=scanned_code,
			qty=qty,
		),
	)


def complete_loading(
	*,
	event: dict[str, Any],
	user: str,
	roles: Iterable[str],
	repository,
	fulfilment_gateway,
	event_store,
) -> dict[str, Any]:
	_require_any(roles, ROUTE_MANAGER_ROLES, "Route loading access is required.")
	route_name = _required_text(event, "route_name")
	get_route_detail(repository, user=user, roles=roles, route_name=route_name)
	return _run_once(
		event=event,
		event_type="Route Loaded",
		reference_doctype="Transport Route",
		reference_name=route_name,
		user=user,
		event_store=event_store,
		action=lambda: fulfilment_gateway.complete_loading(route_name=route_name),
	)


def dispatch_route(
	*,
	event: dict[str, Any],
	user: str,
	roles: Iterable[str],
	repository,
	fulfilment_gateway,
	event_store,
) -> dict[str, Any]:
	_require_any(roles, ROUTE_MANAGER_ROLES, "Route dispatch access is required.")
	route_name = _required_text(event, "route_name")
	get_route_detail(repository, user=user, roles=roles, route_name=route_name)
	event_id = _event_id(event)
	return _run_once(
		event=event,
		event_type="Route Dispatched",
		reference_doctype="Transport Route",
		reference_name=route_name,
		user=user,
		event_store=event_store,
		action=lambda: fulfilment_gateway.dispatch_route(
			route_name=route_name,
			event_id=event_id,
		),
	)


def _run_once(
	*,
	event: dict[str, Any],
	event_type: str,
	reference_doctype: str,
	reference_name: str,
	user: str,
	event_store,
	action: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
	event_id = _event_id(event)
	existing = event_store.find(event_id)
	if existing:
		return {"event_id": event_id, "status": "duplicate", "result": existing["result"]}
	result = action()
	event_store.insert(
		event_id=event_id,
		event_type=event_type,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		user=user,
		device_id=str(event.get("device_id") or ""),
		client_timestamp=str(event.get("client_timestamp") or "") or None,
		payload={key: value for key, value in event.items() if key != "signature_base64"},
		result=result,
	)
	return {"event_id": event_id, "status": "accepted", "result": result}


def _event_id(event: dict[str, Any]) -> str:
	event_id = str(event.get("event_id") or "").strip()
	if not event_id or len(event_id) > 140:
		raise OperationsValidationError("event_id is required and must not exceed 140 characters.")
	return event_id


def _required_text(event: dict[str, Any], fieldname: str) -> str:
	value = str(event.get(fieldname) or "").strip()
	if not value:
		raise OperationsValidationError(f"{fieldname} is required.")
	return value


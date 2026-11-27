from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Iterable

import frappe
from frappe.utils import now_datetime

from neuradix_cassar_ops.operations.exceptions import OperationsValidationError
from neuradix_cassar_ops.operations.mobile_services import get_pick_detail
from neuradix_cassar_ops.operations.timestamps import normalize_client_timestamp


MAX_EVENT_BATCH_SIZE = 100


class FrappeFulfilmentEventStore:
	def find(self, event_id: str) -> dict[str, Any] | None:
		rows = frappe.db.sql(
			"""
			SELECT event_id, result_json
			FROM `tabCassar Fulfilment Event`
			WHERE event_id = %(event_id)s
			LIMIT 1
			""",
			{"event_id": event_id},
			as_dict=True,
		)
		if not rows:
			return None
		return {
			"event_id": rows[0].event_id,
			"result": json.loads(rows[0].result_json or "{}"),
		}

	def insert(
		self,
		*,
		event_id: str,
		event_type: str,
		reference_doctype: str,
		reference_name: str,
		user: str,
		device_id: str,
		client_timestamp: str | None,
		payload: dict[str, Any],
		result: dict[str, Any],
	) -> None:
		frappe.get_doc(
			{
				"doctype": "Cassar Fulfilment Event",
				"event_id": event_id,
				"event_type": event_type,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"user": user,
				"device_id": device_id,
				"client_timestamp": normalize_client_timestamp(client_timestamp),
				"server_timestamp": now_datetime(),
				"payload_json": json.dumps(payload, separators=(",", ":"), sort_keys=True),
				"result_json": json.dumps(result, separators=(",", ":"), sort_keys=True),
			}
		).insert(ignore_permissions=True)


def process_pick_scan(
	*,
	event: dict[str, Any],
	user: str,
	roles: Iterable[str],
	repository,
	gateway,
	event_store,
) -> dict[str, Any]:
	event_id = str(event.get("event_id") or "").strip()
	pick_list_name = str(event.get("pick_list_name") or "").strip()
	scanned_code = str(event.get("scanned_code") or "").strip()
	device_id = str(event.get("device_id") or "").strip()
	client_timestamp = str(event.get("client_timestamp") or "").strip() or None
	try:
		qty = float(event.get("qty") or 0)
	except (TypeError, ValueError) as exception:
		raise OperationsValidationError("qty must be a positive number.") from exception

	if not event_id or len(event_id) > 140:
		raise OperationsValidationError("event_id is required and must not exceed 140 characters.")
	if not pick_list_name:
		raise OperationsValidationError("pick_list_name is required.")
	if not scanned_code:
		raise OperationsValidationError("scanned_code is required.")
	if qty <= 0:
		raise OperationsValidationError("qty must be a positive number.")

	existing = event_store.find(event_id)
	if existing:
		return {"event_id": event_id, "status": "duplicate", "result": existing["result"]}

	get_pick_detail(
		repository,
		user=user,
		roles=roles,
		pick_list_name=pick_list_name,
	)
	result = gateway.record_pick_scan(
		pick_list=pick_list_name,
		scanned_code=scanned_code,
		qty=qty,
	)
	payload = {
		"pick_list_name": pick_list_name,
		"scanned_code": scanned_code,
		"qty": qty,
	}
	event_store.insert(
		event_id=event_id,
		event_type="Pick Scan",
		reference_doctype="Pick List",
		reference_name=pick_list_name,
		user=user,
		device_id=device_id,
		client_timestamp=client_timestamp,
		payload=payload,
		result=result,
	)
	return {"event_id": event_id, "status": "accepted", "result": result}


def process_event_batch(
	*,
	events: list[dict[str, Any]],
	user: str,
	roles: Iterable[str],
	repository,
	gateway,
	event_store,
	event_processors: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
	if not isinstance(events, list):
		raise OperationsValidationError("events must be a JSON array.")
	if len(events) > MAX_EVENT_BATCH_SIZE:
		raise OperationsValidationError(
			f"A sync batch cannot contain more than {MAX_EVENT_BATCH_SIZE} events."
		)

	results = []
	for event in events:
		if not isinstance(event, dict):
			results.append({"event_id": "", "status": "rejected", "error": "Event must be an object."})
			continue
		event_type = str(event.get("event_type") or "")
		if event_type != "pick_scan" and event_type not in (event_processors or {}):
			results.append(
				{
					"event_id": str(event.get("event_id") or ""),
					"status": "rejected",
					"error": "Unsupported event_type.",
				}
			)
			continue
		try:
			if event_type == "pick_scan":
				result = process_pick_scan(
					event=event,
					user=user,
					roles=roles,
					repository=repository,
					gateway=gateway,
					event_store=event_store,
				)
			else:
				result = (event_processors or {})[event_type](event)
			results.append(result)
		except Exception as exception:
			results.append(
				{
					"event_id": str(event.get("event_id") or ""),
					"status": "rejected",
					"error": str(exception),
				}
			)

	return {
		"count": len(results),
		"accepted": sum(row["status"] == "accepted" for row in results),
		"duplicates": sum(row["status"] == "duplicate" for row in results),
		"rejected": sum(row["status"] == "rejected" for row in results),
		"results": results,
	}

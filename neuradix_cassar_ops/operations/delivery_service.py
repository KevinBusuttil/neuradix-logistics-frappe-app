from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any, Iterable

import frappe
from frappe.utils import now_datetime
from frappe.utils.file_manager import save_file

from neuradix_cassar_ops.operations.exceptions import OperationsValidationError
from neuradix_cassar_ops.operations.mobile_services import DELIVERY_ROLES, _require_any, get_route_detail
from neuradix_cassar_ops.operations.timestamps import normalize_client_timestamp


MAX_SIGNATURE_BYTES = 1_048_576
MAX_PHOTO_BYTES = 3_145_728
PNG_DATA_PREFIX = "data:image/png;base64,"
PHOTO_DATA_PREFIXES = ("data:image/png;base64,", "data:image/jpeg;base64,")


class FrappeDeliveryEventStore:
	def find(self, event_id: str) -> dict[str, Any] | None:
		rows = frappe.db.sql(
			"""
			SELECT event_id, result_json
			FROM `tabCassar Delivery Event`
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
		route_name: str,
		stop_name: str,
		customer: str,
		recipient_name: str,
		delivery_result: str,
		failure_reason: str,
		notes: str,
		user: str,
		device_id: str,
		client_timestamp: str | None,
		signature_bytes: bytes,
		photo_bytes: bytes,
		photo_extension: str,
		latitude: float | None,
		longitude: float | None,
		collection_amount: float,
		collection_mode: str,
		delivered_quantities: list[dict[str, Any]],
		workflow_result: dict[str, Any],
	) -> dict[str, Any]:
		delivery_event = frappe.get_doc(
			{
				"doctype": "Cassar Delivery Event",
				"event_id": event_id,
				"transport_route": route_name,
				"route_stop": stop_name,
				"customer": customer,
				"delivery_result": delivery_result,
				"recipient_name": recipient_name,
				"latitude": latitude,
				"longitude": longitude,
				"failure_reason": failure_reason,
				"collection_amount": collection_amount,
				"collection_mode": collection_mode,
				"sales_invoice": _first(workflow_result.get("sales_invoices")),
				"delivery_note": _first(workflow_result.get("delivery_notes")),
				"payment_entry": _first(workflow_result.get("payment_entries")),
				"return_stock_entry": workflow_result.get("return_stock_entry") or "",
				"credit_note": _first(workflow_result.get("credit_notes")),
				"delivered_quantities_json": json.dumps(
					delivered_quantities,
					separators=(",", ":"),
					sort_keys=True,
				),
				"notes": notes,
				"signed_by_user": user,
				"device_id": device_id,
				"client_timestamp": normalize_client_timestamp(client_timestamp),
				"server_timestamp": now_datetime(),
				"result_json": "{}",
			}
		).insert(ignore_permissions=True)
		signature_url = ""
		if signature_bytes:
			file_name = f"pod-{hashlib.sha256(event_id.encode()).hexdigest()[:16]}.png"
			signature = save_file(
				file_name,
				signature_bytes,
				"Cassar Delivery Event",
				delivery_event.name,
				is_private=1,
				df="signature",
			)
			signature_url = signature.file_url
		photo_url = ""
		if photo_bytes:
			photo_name = (
				f"delivery-{hashlib.sha256((event_id + ':photo').encode()).hexdigest()[:16]}"
				f".{photo_extension}"
			)
			photo = save_file(
				photo_name,
				photo_bytes,
				"Cassar Delivery Event",
				delivery_event.name,
				is_private=1,
				df="delivery_photo",
			)
			photo_url = photo.file_url
		result = {
			**workflow_result,
			"delivery_event": delivery_event.name,
			"signature_url": signature_url,
			"delivery_photo_url": photo_url,
			"recipient_name": recipient_name,
			"delivery_result": delivery_result,
		}
		frappe.db.set_value(
			"Cassar Delivery Event",
			delivery_event.name,
			{
				"signature": signature_url,
				"delivery_photo": photo_url,
				"result_json": json.dumps(result, separators=(",", ":"), sort_keys=True),
			},
			update_modified=False,
		)
		return result


def record_proof_of_delivery(
	*,
	event: dict[str, Any],
	user: str,
	roles: Iterable[str],
	repository,
	gateway,
	event_store,
) -> dict[str, Any]:
	_require_any(roles, DELIVERY_ROLES, "Driver delivery access is required.")
	event_id = str(event.get("event_id") or "").strip()
	route_name = str(event.get("route_name") or "").strip()
	stop_name = str(event.get("stop_name") or "").strip()
	recipient_name = str(event.get("recipient_name") or "").strip()
	delivery_result = str(event.get("delivery_result") or "Delivered").strip()
	failure_reason = str(event.get("failure_reason") or "").strip()
	notes = str(event.get("notes") or "").strip()
	device_id = str(event.get("device_id") or "").strip()
	client_timestamp = str(event.get("client_timestamp") or "").strip() or None

	if not event_id or len(event_id) > 140:
		raise OperationsValidationError("event_id is required and must not exceed 140 characters.")
	if not route_name:
		raise OperationsValidationError("route_name is required.")
	if not stop_name:
		raise OperationsValidationError("stop_name is required.")
	if delivery_result not in {"Delivered", "Partially Delivered", "Failed"}:
		raise OperationsValidationError("delivery_result must be Delivered, Partially Delivered, or Failed.")
	if delivery_result != "Failed" and (not recipient_name or len(recipient_name) > 140):
		raise OperationsValidationError(
			"recipient_name is required and must not exceed 140 characters."
		)
	if delivery_result == "Failed" and not failure_reason:
		raise OperationsValidationError("failure_reason is required for a failed delivery.")
	if len(failure_reason) > 140:
		raise OperationsValidationError("failure_reason must not exceed 140 characters.")
	if len(notes) > 2000:
		raise OperationsValidationError("notes must not exceed 2000 characters.")

	existing = event_store.find(event_id)
	if existing:
		return {"event_id": event_id, "status": "duplicate", "result": existing["result"]}

	route = get_route_detail(
		repository,
		user=user,
		roles=roles,
		route_name=route_name,
	)["route"]
	stop = next((row for row in route.get("stops", []) if row.get("name") == stop_name), None)
	if stop is None:
		raise OperationsValidationError(
			f"Route stop {stop_name} does not belong to Transport Route {route_name}."
		)
	if stop.get("status") in {"Delivered", "Partially Delivered", "Failed"}:
		raise OperationsValidationError("This route stop already has a delivery outcome.")

	requirements = (
		gateway.delivery_requirements()
		if hasattr(gateway, "delivery_requirements")
		else {"require_signature": True, "require_gps": False, "enable_driver_collections": True}
	)
	signature_value = str(event.get("signature_base64") or "")
	if requirements["require_signature"] and delivery_result != "Failed" and not signature_value:
		raise OperationsValidationError("A customer signature is required.")
	signature_bytes = _decode_signature(signature_value) if signature_value else b""
	latitude = _coordinate(event.get("latitude"), minimum=-90, maximum=90, label="latitude")
	longitude = _coordinate(event.get("longitude"), minimum=-180, maximum=180, label="longitude")
	if requirements["require_gps"] and (latitude is None or longitude is None):
		raise OperationsValidationError("GPS coordinates are required.")
	photo_bytes, photo_extension = _decode_photo(str(event.get("photo_base64") or ""))
	try:
		collection_amount = float(event.get("collection_amount") or 0)
	except (TypeError, ValueError) as exception:
		raise OperationsValidationError("collection_amount must be a non-negative number.") from exception
	if collection_amount < 0:
		raise OperationsValidationError("collection_amount must be a non-negative number.")
	if collection_amount and not requirements["enable_driver_collections"]:
		raise OperationsValidationError("Driver collections are disabled.")
	delivered_quantities = _quantity_rows(event.get("delivered_quantities"))
	if hasattr(gateway, "complete_delivery"):
		workflow_result = gateway.complete_delivery(
			route_name=route_name,
			stop_name=stop_name,
			delivery_result=delivery_result,
			delivered_quantities=delivered_quantities,
			failure_reason=failure_reason,
			notes=notes,
			collection_amount=collection_amount,
			collection_mode=str(event.get("collection_mode") or "").strip(),
			collection_reference=str(event.get("collection_reference") or "").strip(),
			event_id=event_id,
		)
	else:
		workflow_result = gateway.complete_route_stop(
			route_name=route_name,
			stop_name=stop_name,
			delivered_notes=notes,
		)
	result = event_store.insert(
		event_id=event_id,
		route_name=route_name,
		stop_name=stop_name,
		customer=str(stop.get("customer") or ""),
		recipient_name=recipient_name,
		delivery_result=delivery_result,
		failure_reason=failure_reason,
		notes=notes,
		user=user,
		device_id=device_id,
		client_timestamp=client_timestamp,
		signature_bytes=signature_bytes,
		photo_bytes=photo_bytes,
		photo_extension=photo_extension,
		latitude=latitude,
		longitude=longitude,
		collection_amount=collection_amount,
		collection_mode=str(event.get("collection_mode") or "").strip(),
		delivered_quantities=delivered_quantities,
		workflow_result=workflow_result,
	)
	return {"event_id": event_id, "status": "accepted", "result": result}


def _decode_signature(value: str) -> bytes:
	if not value.startswith(PNG_DATA_PREFIX):
		raise OperationsValidationError("A PNG signature is required.")
	encoded = value[len(PNG_DATA_PREFIX) :]
	if not encoded or len(encoded) > (MAX_SIGNATURE_BYTES * 4 // 3) + 8:
		raise OperationsValidationError("The signature image is empty or too large.")
	try:
		content = base64.b64decode(encoded, validate=True)
	except (binascii.Error, ValueError) as exception:
		raise OperationsValidationError("The signature image is not valid base64 data.") from exception
	if len(content) > MAX_SIGNATURE_BYTES:
		raise OperationsValidationError("The signature image must not exceed 1 MB.")
	if not content.startswith(b"\x89PNG\r\n\x1a\n"):
		raise OperationsValidationError("The signature must be a PNG image.")
	return content


def _decode_photo(value: str) -> tuple[bytes, str]:
	if not value:
		return b"", ""
	prefix = next((candidate for candidate in PHOTO_DATA_PREFIXES if value.startswith(candidate)), "")
	if not prefix:
		raise OperationsValidationError("Delivery photo must be a PNG or JPEG image.")
	encoded = value[len(prefix) :]
	if not encoded or len(encoded) > (MAX_PHOTO_BYTES * 4 // 3) + 8:
		raise OperationsValidationError("The delivery photo is empty or too large.")
	try:
		content = base64.b64decode(encoded, validate=True)
	except (binascii.Error, ValueError) as exception:
		raise OperationsValidationError("The delivery photo is not valid base64 data.") from exception
	if len(content) > MAX_PHOTO_BYTES:
		raise OperationsValidationError("The delivery photo must not exceed 3 MB.")
	if prefix.endswith("png;base64,") and not content.startswith(b"\x89PNG\r\n\x1a\n"):
		raise OperationsValidationError("The delivery photo is not a valid PNG image.")
	if prefix.endswith("jpeg;base64,") and not content.startswith(b"\xff\xd8\xff"):
		raise OperationsValidationError("The delivery photo is not a valid JPEG image.")
	return content, "png" if "png" in prefix else "jpg"


def _coordinate(value: Any, *, minimum: float, maximum: float, label: str) -> float | None:
	if value in (None, ""):
		return None
	try:
		coordinate = float(value)
	except (TypeError, ValueError) as exception:
		raise OperationsValidationError(f"{label} must be numeric.") from exception
	if not minimum <= coordinate <= maximum:
		raise OperationsValidationError(f"{label} is outside its valid range.")
	return coordinate


def _quantity_rows(value: Any) -> list[dict[str, Any]]:
	if value in (None, ""):
		return []
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except json.JSONDecodeError as exception:
			raise OperationsValidationError("delivered_quantities must be valid JSON.") from exception
	if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
		raise OperationsValidationError("delivered_quantities must be a JSON array of objects.")
	return value


def _first(value: Any) -> str:
	return str(value[0]) if isinstance(value, list) and value else ""

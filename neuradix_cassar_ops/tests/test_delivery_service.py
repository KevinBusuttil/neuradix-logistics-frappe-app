from __future__ import annotations

import base64
import unittest

from neuradix_cassar_ops.operations.delivery_service import record_proof_of_delivery
from neuradix_cassar_ops.operations.exceptions import OperationsPermissionError, OperationsValidationError


PNG_SIGNATURE = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nvalid-test-data").decode()


class FakeRepository:
	def get_route(self, *, route_name):
		return {
			"name": route_name,
			"driver_user": "driver@example.com",
			"stops": [
				{
					"name": "STOP-001",
					"customer": "CUSTOMER-001",
					"status": "Queued",
				}
			],
		}


class FakeGateway:
	def __init__(self):
		self.calls = []

	def complete_route_stop(self, **kwargs):
		self.calls.append(kwargs)
		return {
			"route_name": kwargs["route_name"],
			"stop_name": kwargs["stop_name"],
			"route_status": "Closed",
			"stop_status": "Delivered",
		}


class FullLifecycleGateway:
	def __init__(self):
		self.calls = []

	def delivery_requirements(self):
		return {
			"require_signature": True,
			"require_gps": False,
			"enable_driver_collections": True,
		}

	def complete_delivery(self, **kwargs):
		self.calls.append(kwargs)
		return {
			"route_name": kwargs["route_name"],
			"stop_name": kwargs["stop_name"],
			"route_status": "Dispatched",
			"stop_status": kwargs["delivery_result"],
			"delivery_notes": [],
			"credit_notes": [],
		}


class FakeDeliveryEventStore:
	def __init__(self):
		self.rows = {}

	def find(self, event_id):
		return self.rows.get(event_id)

	def insert(self, **kwargs):
		result = {
			**kwargs["workflow_result"],
			"delivery_event": kwargs["event_id"],
			"signature_url": "/private/files/pod.png",
			"recipient_name": kwargs["recipient_name"],
		}
		self.rows[kwargs["event_id"]] = {"event_id": kwargs["event_id"], "result": result}
		return result


def _event(**overrides):
	event = {
		"event_id": "pod-event-001",
		"route_name": "ROUTE-001",
		"stop_name": "STOP-001",
		"recipient_name": "Alex Customer",
		"signature_base64": PNG_SIGNATURE,
		"notes": "Received in good condition",
		"device_id": "tablet-01",
	}
	event.update(overrides)
	return event


class DeliveryServiceTestCase(unittest.TestCase):
	def test_delivery_records_signature_evidence_and_is_idempotent(self) -> None:
		gateway = FakeGateway()
		store = FakeDeliveryEventStore()
		arguments = {
			"event": _event(),
			"user": "driver@example.com",
			"roles": ["Cassar Driver"],
			"repository": FakeRepository(),
			"gateway": gateway,
			"event_store": store,
		}

		first = record_proof_of_delivery(**arguments)
		second = record_proof_of_delivery(**arguments)

		self.assertEqual(first["status"], "accepted")
		self.assertEqual(first["result"]["stop_status"], "Delivered")
		self.assertEqual(second["status"], "duplicate")
		self.assertEqual(len(gateway.calls), 1)

	def test_driver_cannot_deliver_another_drivers_route(self) -> None:
		with self.assertRaises(OperationsPermissionError):
			record_proof_of_delivery(
				event=_event(),
				user="other.driver@example.com",
				roles=["Cassar Driver"],
				repository=FakeRepository(),
				gateway=FakeGateway(),
				event_store=FakeDeliveryEventStore(),
			)

	def test_non_png_signature_is_rejected_before_route_mutation(self) -> None:
		gateway = FakeGateway()
		with self.assertRaises(OperationsValidationError):
			record_proof_of_delivery(
				event=_event(signature_base64="data:image/png;base64,bm90IGEgcG5n"),
				user="driver@example.com",
				roles=["Cassar Driver"],
				repository=FakeRepository(),
				gateway=gateway,
				event_store=FakeDeliveryEventStore(),
			)
		self.assertEqual(gateway.calls, [])

	def test_loader_cannot_record_delivery(self) -> None:
		with self.assertRaises(OperationsPermissionError):
			record_proof_of_delivery(
				event=_event(),
				user="loader@example.com",
				roles=["Cassar Loader"],
				repository=FakeRepository(),
				gateway=FakeGateway(),
				event_store=FakeDeliveryEventStore(),
			)

	def test_partial_delivery_forwards_item_quantities_and_collection(self) -> None:
		gateway = FullLifecycleGateway()
		result = record_proof_of_delivery(
			event=_event(
				delivery_result="Partially Delivered",
				delivered_quantities='[{"sales_order":"SO-001","item_code":"ITEM-001","qty":2}]',
				collection_amount=25,
				collection_mode="Cash",
			),
			user="driver@example.com",
			roles=["Cassar Driver"],
			repository=FakeRepository(),
			gateway=gateway,
			event_store=FakeDeliveryEventStore(),
		)

		self.assertEqual(result["status"], "accepted")
		self.assertEqual(gateway.calls[0]["delivery_result"], "Partially Delivered")
		self.assertEqual(gateway.calls[0]["delivered_quantities"][0]["qty"], 2)
		self.assertEqual(gateway.calls[0]["collection_amount"], 25)

	def test_failed_delivery_requires_reason_but_not_signature(self) -> None:
		gateway = FullLifecycleGateway()
		result = record_proof_of_delivery(
			event=_event(
				delivery_result="Failed",
				failure_reason="Customer closed",
				recipient_name="",
				signature_base64="",
			),
			user="driver@example.com",
			roles=["Cassar Driver"],
			repository=FakeRepository(),
			gateway=gateway,
			event_store=FakeDeliveryEventStore(),
		)

		self.assertEqual(result["result"]["stop_status"], "Failed")
		self.assertEqual(gateway.calls[0]["failure_reason"], "Customer closed")

	def test_invalid_gps_is_rejected_before_workflow_mutation(self) -> None:
		gateway = FullLifecycleGateway()
		with self.assertRaises(OperationsValidationError):
			record_proof_of_delivery(
				event=_event(latitude=95, longitude=14.5),
				user="driver@example.com",
				roles=["Cassar Driver"],
				repository=FakeRepository(),
				gateway=gateway,
				event_store=FakeDeliveryEventStore(),
			)
		self.assertEqual(gateway.calls, [])


if __name__ == "__main__":
	unittest.main()

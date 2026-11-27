from __future__ import annotations

import unittest

from neuradix_cassar_ops.operations.exceptions import OperationsPermissionError
from neuradix_cassar_ops.operations.workflow_service import (
	approve_check,
	claim_check,
	dispatch_route,
	finalize_pick,
	record_load_scan,
)


class FakeRepository:
	def get_pick_list(self, *, pick_list_name):
		return {"name": pick_list_name, "picker": "picker@example.com", "lines": []}

	def get_route(self, *, route_name):
		return {"name": route_name, "driver_user": "", "stops": []}


class FakeGateway:
	def __init__(self):
		self.calls = []

	def finalize_pick(self, **kwargs):
		self.calls.append(("finalize", kwargs))
		return {"picking_status": "Done"}

	def approve_check(self, **kwargs):
		self.calls.append(("approve", kwargs))
		return {"checking_status": "Approved"}

	def claim_check(self, **kwargs):
		self.calls.append(("claim_check", kwargs))
		return {"checking_status": "In Progress"}

	def record_load_scan(self, **kwargs):
		self.calls.append(("load_scan", kwargs))
		return {"route_status": "Loading", "item_code": "ITEM-001"}

	def dispatch_route(self, **kwargs):
		self.calls.append(("dispatch", kwargs))
		return {"route_status": "Dispatched", "stock_entry": "STE-001"}


class FakeEventStore:
	def __init__(self):
		self.rows = {}

	def find(self, event_id):
		return self.rows.get(event_id)

	def insert(self, **kwargs):
		self.rows[kwargs["event_id"]] = {
			"event_id": kwargs["event_id"],
			"result": kwargs["result"],
		}


class WorkflowServiceTestCase(unittest.TestCase):
	def test_finalize_pick_is_idempotent(self) -> None:
		gateway = FakeGateway()
		store = FakeEventStore()
		arguments = {
			"event": {"event_id": "finalize-001", "pick_list_name": "PICK-001"},
			"user": "picker@example.com",
			"roles": ["Cassar Picker"],
			"repository": FakeRepository(),
			"fulfilment_gateway": gateway,
			"event_store": store,
		}

		first = finalize_pick(**arguments)
		second = finalize_pick(**arguments)

		self.assertEqual(first["status"], "accepted")
		self.assertEqual(second["status"], "duplicate")
		self.assertEqual(len(gateway.calls), 1)

	def test_check_approval_is_independently_role_guarded(self) -> None:
		gateway = FakeGateway()
		result = approve_check(
			event={"event_id": "check-001", "pick_list_name": "PICK-001", "notes": "OK"},
			user="checker@example.com",
			roles=["Cassar Checker"],
			fulfilment_gateway=gateway,
			event_store=FakeEventStore(),
		)

		self.assertEqual(result["result"]["checking_status"], "Approved")
		self.assertEqual(gateway.calls[0][1]["checker"], "checker@example.com")
		with self.assertRaises(OperationsPermissionError):
			approve_check(
				event={"event_id": "check-002", "pick_list_name": "PICK-001"},
				user="picker@example.com",
				roles=["Cassar Picker"],
				fulfilment_gateway=gateway,
				event_store=FakeEventStore(),
			)

	def test_checker_can_claim_work_idempotently(self) -> None:
		gateway = FakeGateway()
		store = FakeEventStore()
		arguments = {
			"event": {"event_id": "check-claim-001", "pick_list_name": "PICK-001"},
			"user": "checker@example.com",
			"roles": ["Cassar Checker"],
			"fulfilment_gateway": gateway,
			"event_store": store,
		}

		first = claim_check(**arguments)
		second = claim_check(**arguments)

		self.assertEqual(first["result"]["checking_status"], "In Progress")
		self.assertEqual(second["status"], "duplicate")
		self.assertEqual(len(gateway.calls), 1)

	def test_load_scan_and_dispatch_use_stable_event_ids(self) -> None:
		gateway = FakeGateway()
		store = FakeEventStore()
		scan = record_load_scan(
			event={
				"event_id": "load-scan-001",
				"route_name": "ROUTE-001",
				"scanned_code": "0123456789012",
				"qty": 2,
			},
			user="loader@example.com",
			roles=["Cassar Loader"],
			repository=FakeRepository(),
			fulfilment_gateway=gateway,
			event_store=store,
		)
		dispatched = dispatch_route(
			event={"event_id": "dispatch-001", "route_name": "ROUTE-001"},
			user="loader@example.com",
			roles=["Cassar Loader"],
			repository=FakeRepository(),
			fulfilment_gateway=gateway,
			event_store=store,
		)

		self.assertEqual(scan["result"]["item_code"], "ITEM-001")
		self.assertEqual(dispatched["result"]["stock_entry"], "STE-001")
		self.assertEqual(gateway.calls[-1][1]["event_id"], "dispatch-001")


if __name__ == "__main__":
	unittest.main()

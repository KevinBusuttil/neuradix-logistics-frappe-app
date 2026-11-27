from __future__ import annotations

import unittest

from neuradix_cassar_ops.operations.event_service import process_event_batch, process_pick_scan


class FakeRepository:
	def get_pick_list(self, *, pick_list_name):
		return {"name": pick_list_name, "picker": "picker@example.com", "lines": []}


class FakeGateway:
	def __init__(self):
		self.calls = []

	def record_pick_scan(self, **kwargs):
		self.calls.append(kwargs)
		return {"updated_rows": ["ROW-1"], "custom_picking_status": "In Progress"}


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


def _event(event_id="event-001"):
	return {
		"event_id": event_id,
		"event_type": "pick_scan",
		"pick_list_name": "PICK-001",
		"scanned_code": "ITEM-001",
		"qty": 1,
		"device_id": "tablet-01",
	}


class EventServiceTestCase(unittest.TestCase):
	def test_pick_scan_replay_is_idempotent(self):
		gateway = FakeGateway()
		store = FakeEventStore()
		arguments = {
			"event": _event(),
			"user": "picker@example.com",
			"roles": ["Cassar Picker"],
			"repository": FakeRepository(),
			"gateway": gateway,
			"event_store": store,
		}

		first = process_pick_scan(**arguments)
		second = process_pick_scan(**arguments)

		self.assertEqual(first["status"], "accepted")
		self.assertEqual(second["status"], "duplicate")
		self.assertEqual(len(gateway.calls), 1)

	def test_batch_returns_per_event_results(self):
		result = process_event_batch(
			events=[_event(), {**_event("event-002"), "event_type": "unknown"}],
			user="picker@example.com",
			roles=["Cassar Picker"],
			repository=FakeRepository(),
			gateway=FakeGateway(),
			event_store=FakeEventStore(),
		)

		self.assertEqual(result["accepted"], 1)
		self.assertEqual(result["rejected"], 1)
		self.assertEqual(result["count"], 2)


if __name__ == "__main__":
	unittest.main()

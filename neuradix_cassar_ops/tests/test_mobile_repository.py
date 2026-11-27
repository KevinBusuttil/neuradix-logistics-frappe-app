from __future__ import annotations

import unittest
from unittest.mock import patch

from neuradix_cassar_ops.operations import mobile_repository
from neuradix_cassar_ops.operations.mobile_repository import SQLMobileOperationsRepository


class RecordingDB:
	def __init__(self) -> None:
		self.calls = []

	def sql(self, query, params=None, as_dict=False):
		self.calls.append((query, params or {}, as_dict))
		return []


class MobileRepositoryTestCase(unittest.TestCase):
	def test_pick_queue_is_parameterized_and_user_scoped(self) -> None:
		db = RecordingDB()
		frappe_module = type("Frappe", (), {"db": db})()
		with patch.object(mobile_repository, "frappe", frappe_module):
			rows = SQLMobileOperationsRepository().list_pick_lists(
				user="picker@example.com",
				queue="mine",
				status="In Progress",
				limit=999,
			)

		self.assertEqual(rows, [])
		query, params, _ = db.calls[0]
		self.assertNotIn("picker@example.com", query)
		self.assertIn("custom_picker = %(user)s", query)
		self.assertEqual(params["user"], "picker@example.com")
		self.assertEqual(params["limit"], 200)

	def test_route_queue_uses_insight_transport_tables(self) -> None:
		db = RecordingDB()
		frappe_module = type("Frappe", (), {"db": db})()
		with patch.object(mobile_repository, "frappe", frappe_module):
			SQLMobileOperationsRepository().list_routes(delivery_date="2026-08-27")

		query, params, _ = db.calls[0]
		self.assertIn("`tabTransport Route`", query)
		self.assertIn("`tabTransport Route Stop`", query)
		self.assertIn("'Partially Delivered', 'Failed'", query)
		self.assertEqual(params["delivery_date"], "2026-08-27")

	def test_driver_route_queue_is_scoped_through_erpnext_driver_user(self) -> None:
		db = RecordingDB()
		frappe_module = type("Frappe", (), {"db": db})()
		with patch.object(mobile_repository, "frappe", frappe_module):
			SQLMobileOperationsRepository().list_routes(
				user="driver@example.com",
				driver_only=True,
			)

		query, params, _ = db.calls[0]
		self.assertIn("`tabDriver`", query)
		self.assertIn("driver.user = %(user)s", query)
		self.assertNotIn("driver@example.com", query)
		self.assertEqual(params["user"], "driver@example.com")

	def test_route_detail_uses_parameterized_insight_route_queries(self) -> None:
		class RouteDB(RecordingDB):
			def sql(self, query, params=None, as_dict=False):
				self.calls.append((query, params or {}, as_dict))
				if "FROM `tabTransport Route` route" in query:
					return [{"name": "ROUTE-001", "driver_user": "driver@example.com"}]
				return []

		db = RouteDB()
		frappe_module = type("Frappe", (), {"db": db})()
		with patch.object(mobile_repository, "frappe", frappe_module):
			detail = SQLMobileOperationsRepository().get_route(route_name="ROUTE-001")

		self.assertEqual(detail["name"], "ROUTE-001")
		self.assertEqual(len(db.calls), 4)
		for query, params, _ in db.calls:
			self.assertNotIn("ROUTE-001", query)
			self.assertEqual(params["route_name"], "ROUTE-001")
		self.assertIn("`tabRoute Stop Document`", db.calls[2][0])
		self.assertIn("`tabSales Order Item`", db.calls[3][0])

	def test_load_manifest_is_parameterized_and_reads_insight_load_lines(self) -> None:
		class RouteDB(RecordingDB):
			def sql(self, query, params=None, as_dict=False):
				self.calls.append((query, params or {}, as_dict))
				if "FROM `tabTransport Route` route" in query:
					return [{"name": "ROUTE-001", "driver_user": ""}]
				return []

		db = RouteDB()
		frappe_module = type("Frappe", (), {"db": db})()
		with patch.object(mobile_repository, "frappe", frappe_module):
			manifest = SQLMobileOperationsRepository().get_route_load_manifest(
				route_name="ROUTE-001"
			)

		self.assertEqual(manifest["name"], "ROUTE-001")
		load_query = next(query for query, _, _ in db.calls if "`tabTransport Load Line`" in query)
		self.assertNotIn("ROUTE-001", load_query)
		self.assertIn("load_line.parent = %(route_name)s", load_query)


if __name__ == "__main__":
	unittest.main()

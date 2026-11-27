from __future__ import annotations

import unittest

from neuradix_cassar_ops.operations.exceptions import OperationsPermissionError
from neuradix_cassar_ops.operations.mobile_services import (
	create_insight_plan,
	get_route_detail,
	get_mobile_bootstrap,
	list_pick_queue,
)


class FakeGateway:
	def capabilities(self):
		return {"transport_planning": True, "route_optimization": True, "picking_hub": True}

	def create_plan(self, **kwargs):
		return {"name": "TP-001", **kwargs}


class FakeRepository:
	def list_pick_lists(self, **kwargs):
		return [{"name": "PICK-001", **kwargs}]

	def get_route(self, *, route_name):
		return {"name": route_name, "driver_user": "driver@example.com", "stops": []}


class MobileServicesTestCase(unittest.TestCase):
	def test_bootstrap_is_role_driven_and_reports_insight_capabilities(self) -> None:
		result = get_mobile_bootstrap(
			user="picker@example.com",
			roles=["Cassar Picker"],
			gateway=FakeGateway(),
		)
		self.assertTrue(result["features"]["picking"])
		self.assertFalse(result["features"]["planning"])
		self.assertTrue(result["insight"]["transport_planning"])

	def test_plan_creation_requires_planner_and_uses_gateway(self) -> None:
		with self.assertRaises(OperationsPermissionError):
			create_insight_plan(
				FakeGateway(),
				roles=["Cassar Picker"],
				plan_date="2026-08-27",
				company="Qoffa Demo Ltd",
				source_warehouse="Stores - QDL",
			)

		result = create_insight_plan(
			FakeGateway(),
			roles=["Dispatcher"],
			plan_date="2026-08-27",
			company="Qoffa Demo Ltd",
			source_warehouse="Stores - QDL",
		)
		self.assertEqual(result["plan"]["name"], "TP-001")

	def test_picker_queue_is_scoped_to_current_user(self) -> None:
		result = list_pick_queue(
			FakeRepository(),
			user="picker@example.com",
			roles=["Cassar Picker"],
			queue="mine",
			status=None,
			limit=50,
		)
		self.assertEqual(result["pick_lists"][0]["user"], "picker@example.com")
		self.assertEqual(result["pick_lists"][0]["queue"], "mine")

	def test_driver_route_detail_is_assignment_scoped(self) -> None:
		result = get_route_detail(
			FakeRepository(),
			user="driver@example.com",
			roles=["Cassar Driver"],
			route_name="ROUTE-001",
		)
		self.assertEqual(result["route"]["name"], "ROUTE-001")

		with self.assertRaises(OperationsPermissionError):
			get_route_detail(
				FakeRepository(),
				user="other.driver@example.com",
				roles=["Cassar Driver"],
				route_name="ROUTE-001",
			)

	def test_loader_can_read_routes_but_not_use_delivery_feature(self) -> None:
		result = get_mobile_bootstrap(
			user="loader@example.com",
			roles=["Cassar Loader"],
			gateway=FakeGateway(),
		)
		self.assertTrue(result["features"]["loading"])
		self.assertFalse(result["features"]["delivery"])

	def test_driver_can_deliver_but_cannot_operate_loading_controls(self) -> None:
		result = get_mobile_bootstrap(
			user="driver@example.com",
			roles=["Cassar Driver"],
			gateway=FakeGateway(),
		)
		self.assertFalse(result["features"]["loading"])
		self.assertTrue(result["features"]["delivery"])


if __name__ == "__main__":
	unittest.main()

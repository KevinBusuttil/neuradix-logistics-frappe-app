from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from neuradix_cassar_ops import demo_seed
from neuradix_cassar_ops.demo_seed import (
	DEMO_ITEM_BARCODES,
	DEMO_OPERATIONS_USERS,
	DEMO_PLAN_SPECS,
	_delivery_offset_for_order,
	_ensure_pick_lists,
	_ensure_operations_user,
	_planning_batch_client_order_ids,
)
from neuradix_cassar_ops.operations.mobile_services import (
	CHECKING_ROLES,
	PICKING_ROLES,
	PLANNER_ROLES,
	ROUTE_ROLES,
)


class OperationsDemoSeedContractTestCase(unittest.TestCase):
	def test_demo_password_requires_explicit_nonblank_environment_value(self) -> None:
		for value in (None, "", "   "):
			with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
				if value is not None:
					os.environ["NEURADIX_OPS_DEMO_PASSWORD"] = value
				with self.assertRaisesRegex(RuntimeError, "NEURADIX_OPS_DEMO_PASSWORD"):
					demo_seed._get_demo_operations_password()

	def test_demo_password_is_read_at_execution_without_changing_its_value(self) -> None:
		with patch.dict(os.environ, {"NEURADIX_OPS_DEMO_PASSWORD": " unit-test-value "}):
			self.assertEqual(demo_seed._get_demo_operations_password(), " unit-test-value ")
			os.environ["NEURADIX_OPS_DEMO_PASSWORD"] = "changed-unit-test-value"
			self.assertEqual(demo_seed._get_demo_operations_password(), "changed-unit-test-value")

	def test_missing_demo_password_fails_before_elevation_or_seed_writes(self) -> None:
		frappe_module = SimpleNamespace(
			local=SimpleNamespace(site="unit-test.localhost"),
			set_user=Mock(),
		)
		with patch.dict(os.environ, {}, clear=True), patch.object(
			demo_seed, "frappe", frappe_module
		), patch.object(demo_seed, "seed_demo_site") as seed_site:
			with self.assertRaisesRegex(RuntimeError, "NEURADIX_OPS_DEMO_PASSWORD"):
				demo_seed.seed_demo_operations_data()
		frappe_module.set_user.assert_not_called()
		seed_site.assert_not_called()

	def test_configured_demo_password_does_not_bypass_local_site_guard(self) -> None:
		frappe_module = SimpleNamespace(
			local=SimpleNamespace(site="erp.example.com"),
			set_user=Mock(),
		)
		with patch.dict(os.environ, {"NEURADIX_OPS_DEMO_PASSWORD": "unit-test-value"}), patch.object(
			demo_seed, "frappe", frappe_module
		), patch.object(demo_seed, "seed_demo_site") as seed_site:
			with self.assertRaisesRegex(RuntimeError, "local/UAT-only"):
				demo_seed.seed_demo_operations_data()
		frappe_module.set_user.assert_not_called()
		seed_site.assert_not_called()

	def test_plan_specs_assign_eight_orders_exactly_once(self) -> None:
		assigned_indexes = [
			order_index
			for spec in DEMO_PLAN_SPECS
			for route_indexes in spec.route_order_indexes
			for order_index in route_indexes
		]

		self.assertEqual(sorted(assigned_indexes), list(range(8)))
		self.assertEqual(len(assigned_indexes), len(set(assigned_indexes)))
		for order_index in assigned_indexes:
			self.assertIn(_delivery_offset_for_order(order_index), {0, 1, 2})

	def test_demo_users_cover_every_mobile_role_boundary(self) -> None:
		roles_by_email = {user.email: set(user.roles) for user in DEMO_OPERATIONS_USERS}

		manager_roles = roles_by_email["ops.manager@neuradix.local"]
		self.assertIn("System Manager", manager_roles)
		self.assertFalse(manager_roles.isdisjoint(PLANNER_ROLES))
		self.assertFalse(manager_roles.isdisjoint(PICKING_ROLES))
		self.assertFalse(manager_roles.isdisjoint(CHECKING_ROLES))
		self.assertFalse(manager_roles.isdisjoint(ROUTE_ROLES))
		self.assertFalse(
			roles_by_email["picker.one@neuradix.local"].isdisjoint(PICKING_ROLES)
		)
		self.assertFalse(
			roles_by_email["checker@neuradix.local"].isdisjoint(CHECKING_ROLES)
		)
		self.assertFalse(
			roles_by_email["driver.one@neuradix.local"].isdisjoint(ROUTE_ROLES)
		)

	def test_demo_item_barcodes_are_valid_unique_ean13_values(self) -> None:
		barcodes = list(DEMO_ITEM_BARCODES.values())
		self.assertEqual(len(barcodes), len(set(barcodes)))
		for barcode in barcodes:
			with self.subTest(barcode=barcode):
				self.assertEqual(len(barcode), 13)
				self.assertTrue(barcode.isdigit())
				weighted_sum = sum(
					int(digit) * (1 if index % 2 == 0 else 3)
					for index, digit in enumerate(barcode[:12])
				)
				self.assertEqual(int(barcode[-1]), (-weighted_sum) % 10)

	def test_existing_operations_user_is_not_saved_when_unchanged(self) -> None:
		spec = DEMO_OPERATIONS_USERS[0]
		user = SimpleNamespace(
			enabled=1,
			user_type="System User",
			roles=[SimpleNamespace(role=role) for role in spec.roles],
			append=Mock(),
			save=Mock(),
		)
		frappe_module = SimpleNamespace(
			db=SimpleNamespace(exists=Mock(return_value=True)),
			get_doc=Mock(return_value=user),
		)
		with patch.object(demo_seed, "frappe", frappe_module), patch.object(
			demo_seed,
			"update_password",
		) as update_password:
			_ensure_operations_user(spec, "unit-test-value")

		user.append.assert_not_called()
		user.save.assert_not_called()
		update_password.assert_called_once_with(
			spec.email,
			"unit-test-value",
			logout_all_sessions=False,
		)

	def test_manual_planning_batch_builds_stable_unique_order_ids(self) -> None:
		order_ids = _planning_batch_client_order_ids(count=10, batch_code="uat-b")

		self.assertEqual(len(order_ids), 10)
		self.assertEqual(len(set(order_ids)), 10)
		self.assertEqual(order_ids[0], "NEURADIX-OPS-PICK-UAT-UAT-B-001")
		self.assertEqual(order_ids[-1], "NEURADIX-OPS-PICK-UAT-UAT-B-010")

	def test_manual_planning_batch_rejects_unsafe_input(self) -> None:
		for count, batch_code in ((0, "A"), (101, "A"), (10, "bad code")):
			with self.subTest(count=count, batch_code=batch_code):
				with self.assertRaises(ValueError):
					_planning_batch_client_order_ids(
						count=count,
						batch_code=batch_code,
					)

	def test_demo_seed_preserves_submitted_pick_lists(self) -> None:
		pick_list = SimpleNamespace(
			name="PICK-001",
			docstatus=1,
			locations=[SimpleNamespace(qty=1, picked_qty=1)],
			save=Mock(),
		)
		frappe_module = SimpleNamespace(
			get_all=Mock(return_value=["SO-001"]),
			get_doc=Mock(return_value=pick_list),
		)
		with patch.object(demo_seed, "frappe", frappe_module), patch.object(
			demo_seed,
			"_find_demo_pick_list",
			return_value="PICK-001",
		), patch.object(demo_seed, "_apply_pick_state") as apply_pick_state:
			pick_lists = _ensure_pick_lists({"PLAN-001": ["ROUTE-001"]})

		self.assertEqual(pick_lists, ["PICK-001"])
		pick_list.save.assert_not_called()
		apply_pick_state.assert_not_called()


if __name__ == "__main__":
	unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from neuradix_cassar_ops.operations import fulfilment_gateway
from neuradix_cassar_ops.operations.exceptions import OperationsValidationError
from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway


class FulfilmentGatewayRulesTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.available = {
			("SO-001", "ITEM-001"): 5.0,
			("SO-001", "ITEM-002"): 3.0,
		}

	def test_full_and_failed_delivery_ignore_client_quantity_overrides(self) -> None:
		self.assertEqual(
			ERPNextFulfilmentGateway._validated_delivery_totals(
				delivery_result="Delivered",
				requested={},
				available=self.available,
			),
			self.available,
		)
		self.assertEqual(
			ERPNextFulfilmentGateway._validated_delivery_totals(
				delivery_result="Failed",
				requested={("SO-001", "ITEM-001"): 5.0},
				available=self.available,
			),
			{},
		)

	def test_partial_delivery_requires_a_real_split(self) -> None:
		valid = ERPNextFulfilmentGateway._validated_delivery_totals(
			delivery_result="Partially Delivered",
			requested={("SO-001", "ITEM-001"): 2.0},
			available=self.available,
		)
		self.assertEqual(valid, {("SO-001", "ITEM-001"): 2.0})

		for requested in ({}, self.available):
			with self.subTest(requested=requested), self.assertRaises(OperationsValidationError):
				ERPNextFulfilmentGateway._validated_delivery_totals(
					delivery_result="Partially Delivered",
					requested=requested,
					available=self.available,
				)

	def test_partial_delivery_rejects_unknown_and_excess_quantities(self) -> None:
		for requested in (
			{("SO-404", "ITEM-001"): 1.0},
			{("SO-001", "ITEM-001"): 6.0},
		):
			with self.subTest(requested=requested), self.assertRaises(OperationsValidationError):
				ERPNextFulfilmentGateway._validated_delivery_totals(
					delivery_result="Partially Delivered",
					requested=requested,
					available=self.available,
				)

	def test_unlink_route_document_uses_scoped_child_row_delete(self) -> None:
		fake_db = Mock()
		with patch.object(fulfilment_gateway.frappe, "db", fake_db):
			ERPNextFulfilmentGateway._unlink_route_document(
				route_name="ROUTE-001",
				ref_type="Delivery Note",
				ref_name="DN-001",
			)

		query, values = fake_db.sql.call_args.args
		self.assertIn("DELETE FROM `tabRoute Stop Document`", query)
		self.assertEqual(
			values,
			{
				"route_name": "ROUTE-001",
				"ref_type": "Delivery Note",
				"ref_name": "DN-001",
			},
		)

	def test_operations_defaults_are_read_from_single_settings_in_one_query(self) -> None:
		fake_db = Mock()
		fake_db.sql.return_value = [
			type("Row", (), {"field": "default_company", "value": "Cassar Ltd"})(),
			type(
				"Row",
				(),
				{"field": "default_source_warehouse", "value": "Stores - CC"},
			)(),
		]
		with patch.object(fulfilment_gateway.frappe, "db", fake_db):
			defaults = ERPNextFulfilmentGateway.operations_defaults()

		self.assertEqual(
			defaults,
			{
				"company": "Cassar Ltd",
				"source_warehouse": "Stores - CC",
				"collection_mode": "",
			},
		)
		self.assertEqual(fake_db.sql.call_count, 1)


if __name__ == "__main__":
	unittest.main()

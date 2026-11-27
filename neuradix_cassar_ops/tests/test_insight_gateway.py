from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from neuradix_cassar_ops.operations import insight_gateway
from neuradix_cassar_ops.operations.exceptions import OperationsValidationError
from neuradix_cassar_ops.operations.insight_gateway import InsightTransportGateway


class InsightGatewayTestCase(unittest.TestCase):
	def test_preview_delegates_to_insight_public_api(self) -> None:
		module = SimpleNamespace(preview_auto_generate_routes=Mock(return_value={"clusters": [1]}))
		with patch.object(insight_gateway.importlib, "import_module", return_value=module) as importer:
			result = InsightTransportGateway().preview_routes(
				plan_name="TP-001",
				vehicles_json='["VAN-1"]',
				nl_instructions="Keep Gozo separate",
			)

		self.assertEqual(result, {"clusters": [1]})
		importer.assert_called_once_with("insight.api.auto_generate_routes")
		module.preview_auto_generate_routes.assert_called_once_with(
			plan_name="TP-001",
			vehicles_json='["VAN-1"]',
			nl_instructions="Keep Gozo separate",
		)

	def test_preview_uses_compatibility_adapter_when_insight_returns_no_stops(self) -> None:
		fallback = {
			"clusters": [],
			"total_stops": 0,
			"parsed_constraints": [{"type": "driver_area"}],
			"warnings": ["No undelivered Sales Orders with geocoordinates found."],
		}
		compatible = {"clusters": [{"stops": [{"ref_name": "SO-001"}]}], "total_stops": 1}
		module = SimpleNamespace(preview_auto_generate_routes=Mock(return_value=fallback))
		with patch.object(
			insight_gateway.importlib,
			"import_module",
			return_value=module,
		), patch.object(
			InsightTransportGateway,
			"_preview_routes_with_frappe_compatibility",
			return_value=compatible,
		) as compatibility_adapter:
			result = InsightTransportGateway.preview_routes(
				plan_name="TP-001",
				vehicles_json='["VAN-1"]',
				nl_instructions="Keep Gozo separate",
			)

		self.assertEqual(result, compatible)
		compatibility_adapter.assert_called_once_with(
			module=module,
			plan_name="TP-001",
			vehicles_json='["VAN-1"]',
			parsed_constraints=[{"type": "driver_area"}],
			fallback=fallback,
		)

	def test_pick_scan_delegates_to_insight_picking_hub(self) -> None:
		module = SimpleNamespace(record_pick_scan=Mock(return_value={"custom_picking_status": "Done"}))
		with patch.object(insight_gateway.importlib, "import_module", return_value=module):
			result = InsightTransportGateway().record_pick_scan(
				pick_list="PICK-001",
				scanned_code="ITEM-001",
				qty=2,
			)

		self.assertEqual(result["custom_picking_status"], "Done")
		module.record_pick_scan.assert_called_once_with(
			pick_list="PICK-001",
			scanned_code="ITEM-001",
			qty=2,
		)

	def test_apply_routes_rejects_duplicate_plan_routes(self) -> None:
		frappe_module = SimpleNamespace(
			db=SimpleNamespace(sql=Mock(return_value=[SimpleNamespace(route="ROUTE-001")])),
		)
		with patch.object(insight_gateway, "frappe", frappe_module), patch.object(
			insight_gateway.importlib,
			"import_module",
		) as importer:
			with self.assertRaises(OperationsValidationError):
				InsightTransportGateway.apply_routes(
					plan_name="PLAN-001",
					clusters=[{"stops": [{"customer": "CUSTOMER-001"}]}],
				)

		importer.assert_not_called()

	def test_apply_routes_normalizes_insight_source_links(self) -> None:
		module = SimpleNamespace(
			apply_auto_generate_routes=Mock(
				return_value={"created_routes": ["ROUTE-001"], "warnings": []}
			)
		)
		frappe_module = SimpleNamespace(
			db=SimpleNamespace(sql=Mock(return_value=[])),
		)
		clusters = [{"stops": [{"ref_type": "Sales Order", "ref_name": "SO-001"}]}]
		with patch.object(insight_gateway, "frappe", frappe_module), patch.object(
			insight_gateway.importlib,
			"import_module",
			return_value=module,
		), patch.object(
			InsightTransportGateway,
			"_ensure_route_source_links",
		) as normalize:
			result = InsightTransportGateway.apply_routes(
				plan_name="PLAN-001",
				clusters=clusters,
				enhance=True,
			)

		self.assertEqual(result["created_routes"], ["ROUTE-001"])
		module.apply_auto_generate_routes.assert_called_once_with(
			plan_name="PLAN-001",
			clusters_json='[{"stops": [{"ref_type": "Sales Order", "ref_name": "SO-001"}]}]',
			trigger_enhance=1,
		)
		normalize.assert_called_once_with(created_routes=["ROUTE-001"], clusters=clusters)

	def test_apply_routes_recovers_insight_plan_timestamp_conflict(self) -> None:
		class FakeDB:
			def __init__(self) -> None:
				self.sql_calls = 0
				self.set_value = Mock()

			def sql(self, query, values=None, as_dict=False):
				self.sql_calls += 1
				if self.sql_calls == 1:
					return []
				return [SimpleNamespace(route="ROUTE-001")]

		db = FakeDB()
		module = SimpleNamespace(
			apply_auto_generate_routes=Mock(
				side_effect=insight_gateway.TimestampMismatchError("stale plan")
			)
		)
		frappe_module = SimpleNamespace(db=db)
		clusters = [{"stops": [{"ref_type": "Sales Order", "ref_name": "SO-001"}]}]
		with patch.object(insight_gateway, "frappe", frappe_module), patch.object(
			insight_gateway.importlib,
			"import_module",
			return_value=module,
		), patch.object(
			InsightTransportGateway,
			"_ensure_route_source_links",
		) as normalize:
			result = InsightTransportGateway.apply_routes(
				plan_name="PLAN-001",
				clusters=clusters,
			)

		self.assertEqual(result["created_routes"], ["ROUTE-001"])
		db.set_value.assert_called_once_with("Transport Plan", "PLAN-001", "status", "Planned")
		normalize.assert_called_once_with(created_routes=["ROUTE-001"], clusters=clusters)

	def test_pick_list_creation_reuses_existing_route_result(self) -> None:
		class FakeDB:
			def sql(self, query, params=None, as_dict=False):
				if "tabPick List" in query:
					return [SimpleNamespace(name="PICK-001", custom_transport_route="ROUTE-001")]
				return []

		frappe_module = SimpleNamespace(
			db=FakeDB(),
			throw=Mock(side_effect=AssertionError("unexpected validation")),
			ValidationError=ValueError,
		)
		with patch.object(insight_gateway, "frappe", frappe_module), patch.object(
			insight_gateway.importlib,
			"import_module",
		) as importer:
			result = InsightTransportGateway().create_pick_lists(
				plan_name="TP-001",
				routes=["ROUTE-001"],
			)

		self.assertEqual(result, {"ROUTE-001": ["PICK-001"]})
		importer.assert_not_called()

	def test_delivery_completion_updates_insight_stop_and_documents(self) -> None:
		stop = SimpleNamespace(name="STOP-001", status="Queued", delivered_notes="")
		other_stop = SimpleNamespace(name="STOP-002", status="Delivered", delivered_notes="")
		document = SimpleNamespace(
			stop="STOP-001",
			ref_name="SO-001",
			delivery_status="Undelivered",
			notes="",
		)
		route = SimpleNamespace(
			name="ROUTE-001",
			status="Dispatched",
			stops=[stop, other_stop],
			stop_documents=[document],
			save=Mock(),
		)
		frappe_module = SimpleNamespace(get_doc=Mock(return_value=route))
		with patch.object(insight_gateway, "frappe", frappe_module):
			result = InsightTransportGateway().complete_route_stop(
				route_name="ROUTE-001",
				stop_name="STOP-001",
				delivered_notes="Signed by Alex",
			)

		self.assertEqual(stop.status, "Delivered")
		self.assertEqual(document.delivery_status, "Delivered")
		self.assertEqual(route.status, "Closed")
		self.assertEqual(result["updated_documents"], ["SO-001"])
		route.save.assert_called_once_with(ignore_permissions=True)

	def test_partial_delivery_keeps_route_dispatched_and_marks_stop_terminal(self) -> None:
		stop = SimpleNamespace(
			name="STOP-001",
			status="On the way",
			delivered_notes="",
			failure_reason="",
		)
		document = SimpleNamespace(
			stop="STOP-001",
			ref_name="SO-001",
			delivery_status="Undelivered",
			notes="",
			payment_status="Unpaid",
			payment_mode="",
			customer_payment_ref="",
		)
		route = SimpleNamespace(
			name="ROUTE-001",
			status="Dispatched",
			stops=[stop],
			stop_documents=[document],
			save=Mock(),
		)
		frappe_module = SimpleNamespace(get_doc=Mock(return_value=route))
		with patch.object(insight_gateway, "frappe", frappe_module):
			result = InsightTransportGateway().record_route_stop_outcome(
				route_name="ROUTE-001",
				stop_name="STOP-001",
				delivery_result="Partially Delivered",
				delivered_notes="Two cases short",
			)

		self.assertEqual(stop.status, "Partially Delivered")
		self.assertEqual(route.status, "Dispatched")
		self.assertEqual(result["stop_status"], "Partially Delivered")

	def test_custom_failure_reason_maps_to_insight_other_option(self) -> None:
		stop = SimpleNamespace(
			name="STOP-001",
			status="On the way",
			delivered_notes="",
			failure_reason="",
		)
		route = SimpleNamespace(
			name="ROUTE-001",
			status="Dispatched",
			stops=[stop],
			stop_documents=[],
			save=Mock(),
		)
		frappe_module = SimpleNamespace(get_doc=Mock(return_value=route))
		with patch.object(insight_gateway, "frappe", frappe_module):
			InsightTransportGateway.record_route_stop_outcome(
				route_name="ROUTE-001",
				stop_name="STOP-001",
				delivery_result="Failed",
				failure_reason="Customer unavailable",
			)

		self.assertEqual(stop.failure_reason, "Other")
		self.assertEqual(stop.status, "Failed")


if __name__ == "__main__":
	unittest.main()

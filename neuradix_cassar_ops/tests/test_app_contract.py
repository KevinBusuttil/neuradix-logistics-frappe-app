from __future__ import annotations

import json
import unittest
from pathlib import Path

from neuradix_cassar_ops import hooks


class AppContractTestCase(unittest.TestCase):
	def test_required_apps_match_the_dedicated_cassar_stack(self) -> None:
		self.assertEqual(
			hooks.required_apps,
			["erpnext", "insight", "neuradix", "neuradix_cassarcamilleri"],
		)

	def test_operations_app_has_no_scheduler_or_external_transport_client(self) -> None:
		self.assertFalse(hasattr(hooks, "scheduler_events"))
		package_root = Path(__file__).resolve().parents[1]
		runtime_sources = [
			path
			for path in package_root.rglob("*.py")
			if "tests" not in path.parts and "__pycache__" not in path.parts
		]
		combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in runtime_sources)
		for forbidden in ("requests.get", "requests.post", "soap", "ax_delta"):
			with self.subTest(forbidden=forbidden):
				self.assertNotIn(forbidden, combined)

	def test_operations_app_does_not_modify_or_vendor_insight(self) -> None:
		package_root = Path(__file__).resolve().parents[1]
		self.assertFalse((package_root / "insight").exists())

	def test_insight_is_the_only_transport_plan_model(self) -> None:
		package_root = Path(__file__).resolve().parents[1]
		self.assertFalse(
			(
				package_root
				/ "neuradix_cassar_ops"
				/ "doctype"
				/ "cassar_transport_plan"
				/ "cassar_transport_plan.json"
			).exists()
		)
		self.assertTrue(
			(package_root / "operations" / "insight_gateway.py").exists()
		)

	def test_delivery_evidence_has_a_dedicated_immutable_doctype(self) -> None:
		package_root = Path(__file__).resolve().parents[1]
		doctype_path = (
			package_root
			/ "neuradix_cassar_ops"
			/ "doctype"
			/ "cassar_delivery_event"
			/ "cassar_delivery_event.json"
		)
		self.assertTrue(doctype_path.exists())
		self.assertIn("signature", doctype_path.read_text(encoding="utf-8"))

	def test_workspace_links_match_the_execution_documents(self) -> None:
		package_root = Path(__file__).resolve().parents[1]
		workspace_path = (
			package_root
			/ "neuradix_cassar_ops"
			/ "workspace"
			/ "cassar_operations"
			/ "cassar_operations.json"
		)
		workspace = json.loads(workspace_path.read_text(encoding="utf-8"))
		links = {
			row["label"]: row.get("link_to")
			for row in workspace["links"]
			if row.get("type") == "Link"
		}
		self.assertNotIn("Delivery Trips", links)
		self.assertEqual(links["Transport Routes"], "Transport Route")
		self.assertEqual(links["Sales Invoices"], "Sales Invoice")
		self.assertEqual(links["Stock Transfers"], "Stock Entry")
		self.assertEqual(links["Delivery Events"], "Cassar Delivery Event")


if __name__ == "__main__":
	unittest.main()

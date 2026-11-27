from __future__ import annotations

import frappe

from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway
from neuradix_cassar_ops.operations.insight_gateway import InsightTransportGateway
from neuradix_cassar_ops.operations.mobile_services import get_mobile_bootstrap


@frappe.whitelist()
def get_bootstrap() -> dict:
	payload = get_mobile_bootstrap(
		user=frappe.session.user,
		roles=frappe.get_roles(frappe.session.user),
		gateway=InsightTransportGateway(),
	)
	payload["delivery_requirements"] = ERPNextFulfilmentGateway().delivery_requirements()
	payload["defaults"] = ERPNextFulfilmentGateway.operations_defaults()
	return payload

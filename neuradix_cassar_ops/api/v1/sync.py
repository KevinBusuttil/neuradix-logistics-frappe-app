from __future__ import annotations

import json

import frappe

from neuradix_cassar_ops.operations.event_service import (
	FrappeFulfilmentEventStore,
	process_event_batch,
)
from neuradix_cassar_ops.operations.delivery_service import (
	FrappeDeliveryEventStore,
	record_proof_of_delivery,
)
from neuradix_cassar_ops.operations.fulfilment_gateway import ERPNextFulfilmentGateway
from neuradix_cassar_ops.operations.insight_gateway import InsightTransportGateway
from neuradix_cassar_ops.operations.mobile_repository import SQLMobileOperationsRepository
from neuradix_cassar_ops.operations.workflow_service import record_load_scan


@frappe.whitelist(methods=["POST"])
def push_event_batch(events_json: str) -> dict:
	events = json.loads(events_json or "[]")
	user = frappe.session.user
	roles = frappe.get_roles(user)
	repository = SQLMobileOperationsRepository()
	insight_gateway = InsightTransportGateway()
	fulfilment_gateway = ERPNextFulfilmentGateway(insight_gateway)
	fulfilment_event_store = FrappeFulfilmentEventStore()
	return process_event_batch(
		events=events,
		user=user,
		roles=roles,
		repository=repository,
		gateway=insight_gateway,
		event_store=fulfilment_event_store,
		event_processors={
			"load_scan": lambda event: record_load_scan(
				event=event,
				user=user,
				roles=roles,
				repository=repository,
				fulfilment_gateway=fulfilment_gateway,
				event_store=fulfilment_event_store,
			),
			"proof_of_delivery": lambda event: record_proof_of_delivery(
				event=event,
				user=user,
				roles=roles,
				repository=repository,
				gateway=fulfilment_gateway,
				event_store=FrappeDeliveryEventStore(),
			),
		},
	)

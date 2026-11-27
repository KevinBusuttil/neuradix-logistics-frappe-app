# Neuradix Cassar Operations Context

This repository is the CassarCamilleri-only backend facade for warehouse and delivery execution. It is installed on the same Frappe site used by Neuradix POS and the customer portal.

Repository: https://github.com/KevinBusuttil/neuradix-logistics-frappe-app

Paths below are relative to this repository unless prefixed with `../` or `$BENCH_ROOT`. Read this map before searching; update it when a new location is discovered.

## First Places To Look

- Operator and UAT guide: `docs/POS_TO_DELIVERY_UAT.md`
- Original functional specification: `../neuradix_cassarcamilleri/docs/cassarcamilleri_warehouse_transport_functional_spec.md`
- Public API entry points: `neuradix_cassar_ops/api/v1`
- ERPNext lifecycle gateway: `neuradix_cassar_ops/operations/fulfilment_gateway.py`
- Insight boundary adapter: `neuradix_cassar_ops/operations/insight_gateway.py`
- Read-optimized SQL repository: `neuradix_cassar_ops/operations/mobile_repository.py`
- Workflow and role rules: `neuradix_cassar_ops/operations/workflow_service.py`
- Offline idempotency: `neuradix_cassar_ops/operations/event_service.py`
- Client timestamp normalization: `neuradix_cassar_ops/operations/timestamps.py`
- Delivery evidence and validation: `neuradix_cassar_ops/operations/delivery_service.py`
- Local/UAT demo, manual planning-order batch, and end-to-end assertions: `neuradix_cassar_ops/demo_seed.py`
- Flutter application: [neuradix-logistics-app](https://github.com/KevinBusuttil/neuradix-logistics-app); local checkout convention `../neuradix-cassar-ops`
- Unit tests: `neuradix_cassar_ops/tests`
- Repository test trigger: `run_tests.sh`
- Whole-solution trigger: `$BENCH_ROOT/run_all_tests.sh`

## Testing And Local Credentials

- `./run_tests.sh` discovers the complete app unit suite; it supports `BENCH_ROOT`, `PYTHON_BIN`, and additional unittest options. It detects an enclosing Frappe bench or the sibling bench folders listed in the README.
- The existing bench `run_all_tests.sh` runs the combined backend/frontend regression suite. Set `BENCH_ROOT` when documenting or invoking that external runner.
- `neuradix_cassar_ops/tests/test_test_runner.py` checks relocated checkouts, bench/interpreter overrides, and failure messages.
- Demo seeding requires a private `NEURADIX_OPS_DEMO_PASSWORD` at execution; imports and unit tests do not. Seed output omits the password. The local/UAT site guard remains mandatory.
- `.gitignore` excludes bytecode, build output, local config, credentials, and editor files.

## Implemented Lifecycle

- Read and create Insight Transport Plans, preview/apply Insight routes, and create ERPNext Pick Lists through a narrow adapter.
- Claim, scan, and complete Pick Lists with stable event IDs and bounded offline replay.
- Require an independent checker when configured; support checker claim, approve, and reject/rework.
- Build route load manifests, scan loading quantities, enforce completeness, and dispatch.
- Move dispatched stock from the source warehouse into the configured vehicle/transit warehouse.
- Create dispatch Sales Invoices and draft Delivery Notes from submitted Sales Orders.
- Record full, partial, and failed delivery outcomes with signature, recipient, optional photo/GPS, notes, and collections.
- Submit delivered quantities, return undelivered stock, issue credit notes, create Payment Entries, and close routes/plans when all stops are terminal.
- Persist immutable `Cassar Fulfilment Event` and `Cassar Delivery Event` audit records.

## Architecture Rules

- ERPNext owns Item, Customer, Warehouse, stock ledger, Sales Order, Pick List, Delivery Note, Sales Invoice, Stock Entry, Payment Entry, Driver, and Vehicle.
- Insight owns Transport Plan, Transport Route, route generation, optimization, and its own AX behavior. Do not modify Insight from this project.
- This app owns the Cassar execution facade, role gates, event idempotency, mobile assignments, delivery evidence, and ERPNext lifecycle orchestration.
- No AX endpoint, scheduler, SOAP client, or direct network integration belongs in this app.
- Read-heavy queues use parameterized `frappe.db.sql`; document APIs are reserved for lifecycle transactions.
- API writes require authentication and do not call `frappe.db.commit()` directly.
- The Insight adapter includes narrowly scoped compatibility handling for Frappe 16 API-call and stale-document behavior, while continuing to use Insight's algorithms and doctypes.
- `neuradix_cassar_ops.demo_seed` functions are manual and restricted to local/UAT sites. They must never be wired into production startup or migrate hooks.

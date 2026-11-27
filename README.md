# Neuradix Cassar Operations

Source: [neuradix-logistics-frappe-app](https://github.com/KevinBusuttil/neuradix-logistics-frappe-app). Matching Flutter client: [neuradix-logistics-app](https://github.com/KevinBusuttil/neuradix-logistics-app).

CassarCamilleri-specific warehouse picking, checking, loading, dispatch, and delivery execution for the shared Neuradix/ERPNext backend.

ERPNext owns stock and accounting documents. Insight owns `Transport Plan`, `Transport Route`, route optimization, and its planning rules. This app is the authenticated execution facade used by the Flutter operations app. It does not call AX and does not modify Insight's AX behavior.

## Implemented Flow

1. Neuradix POS submits a standard ERPNext Sales Order.
2. A planner creates an Insight Transport Plan, previews routes, applies the plan, and creates ERPNext Pick Lists.
3. A picker claims a Pick List, scans item codes or barcodes, and completes picking.
4. A different checker claims and approves the completed Pick List, or rejects it with a reason.
5. A loader prepares the route manifest, scans every package/item, completes loading, and dispatches the route.
6. Dispatch transfers stock to the vehicle/transit warehouse, submits Sales Invoices when configured, and creates draft Delivery Notes.
7. A driver records a full, partial, or failed outcome with recipient details, signature, optional photo/GPS, and optional collection.
8. Full delivery submits the Delivery Note. Partial or failed delivery returns undelivered stock and creates the required credit note. The route and plan close when every stop is terminal.

Every mobile write uses a stable event ID, so retrying a scan, workflow action, or proof-of-delivery request is idempotent.

## Configuration

Open the `Cassar Operations` workspace in Frappe Desk, then configure `Cassar Operations Settings`:

- Default company and source warehouse.
- Default vehicle transit, returns, and damaged warehouses.
- Independent checker requirement and picker/checker separation.
- Invoice-on-dispatch behavior and maximum stops per plan.
- Shipping-address, signature, GPS, and driver-collection requirements.
- Default collection mode.

Use Insight's `Transport Manager` or `Dispatcher` roles for planners. Use `Cassar Picker`, `Cassar Checker`, `Cassar Loader`, and `Cassar Driver` for the mobile queues. `Cassar Operations Manager` has the complete operations view.

## Local UAT

The repeatable local dataset and private credential setup are documented in [POS_TO_DELIVERY_UAT.md](docs/POS_TO_DELIVERY_UAT.md).

Set `BENCH_ROOT` to your local Frappe bench when it cannot be discovered from an enclosing bench or a sibling `neuradix-bench` / `neuradix-cloud-bench` directory. `PYTHON_BIN` can override its Python interpreter.

Run all this app's unit tests from the single repository trigger:

```bash
./run_tests.sh
```

Run migration, real Insight planning, and all delivery outcomes:

```bash
"$BENCH_ROOT"/run_bench_smoke.sh
```

Run the complete backend and frontend regression suite:

```bash
"$BENCH_ROOT"/run_all_tests.sh
```

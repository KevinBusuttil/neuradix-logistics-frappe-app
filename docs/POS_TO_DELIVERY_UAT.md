# POS-to-Delivery UAT Guide

This guide validates the complete CassarCamilleri flow on the single local backend at `$BENCH_ROOT` (set this environment variable to your bench directory) and site `neuradix-cassar.localhost`.

## Architecture

- Neuradix POS creates submitted ERPNext Sales Orders.
- Insight owns Transport Plans, Transport Routes, route optimization, and its internal integrations.
- `neuradix_cassar_ops` turns Insight plans/routes into controlled warehouse and delivery work.
- ERPNext remains the source of truth for stock and accounting through Pick Lists, Stock Entries, Delivery Notes, Sales Invoices, Payment Entries, Drivers, Vehicles, Items, and Warehouses.
- The POS, customer portal, and operations Flutter apps use one site and separate authenticated API namespaces.

## Start The Backend

```bash
cd "$BENCH_ROOT"
source env/bin/activate
bench start
```

Use these backend URLs:

- Browser or desktop app on this Mac: `http://neuradix-cassar.localhost:8008` or `http://127.0.0.1:8008`.
- Android emulator: `http://10.0.2.2:8008`.
- Physical Android/iOS device: `http://<MAC_LAN_IP>:8008`, while both devices are on the same network.
- Production later: the shared Cassar ERP hostname, such as `https://erp.cassarcamilleri.com`.

## Start The Operations App

List Flutter devices first:

```bash
cd /path/to/neuradix-logistics-app
flutter devices
```

Run on the Android emulator:

```bash
flutter run -d <ANDROID_DEVICE_ID> \
  --dart-define=OPS_BACKEND_URL=http://10.0.2.2:8008
```

Run on macOS or another desktop target:

```bash
flutter run -d macos \
  --dart-define=OPS_BACKEND_URL=http://127.0.0.1:8008
```

The backend URL is editable on the login screen. The compile-time value only provides the initial default.

## Demo Data And Credentials

Set `NEURADIX_OPS_DEMO_PASSWORD` privately in the shell running the seed commands. The operations seed requires this value and has no built-in password. Keep it out of source control and command output. For example, in Bash:

```bash
read -r -s -p "Operations UAT password: " NEURADIX_OPS_DEMO_PASSWORD
export NEURADIX_OPS_DEMO_PASSWORD
```

Rebuild the repeatable local data:

```bash
cd "$BENCH_ROOT"
bench --site neuradix-cassar.localhost execute \
  neuradix.pos.demo_seed.assert_demo_site_ready
bench --site neuradix-cassar.localhost execute \
  neuradix_cassar_ops.demo_seed.assert_demo_operations_ready
```

| User | Primary UAT responsibility |
| --- | --- |
| `ops.manager@neuradix.local` | Full operations and Desk access |
| `transport.planner@neuradix.local` | Plan, preview, apply routes, create Pick Lists |
| `picker.one@neuradix.local` | Claim and scan picking work |
| `picker.two@neuradix.local` | Second picker and assignment testing |
| `checker@neuradix.local` | Independent check approval/rejection |
| `loader@neuradix.local` | Manifest scan, loading completion, dispatch |
| `driver.one@neuradix.local` | Assigned route delivery and proof of delivery |
| `driver.two@neuradix.local` | Second driver and assignment testing |

Use the locally configured POS and customer-portal credentials for their respective UAT flows; obtain them from the environment owner.

Create a clean batch of 10 submitted, geocoded, deliberately unplanned orders for manual planning:

```bash
bench --site neuradix-cassar.localhost execute \
  neuradix_cassar_ops.demo_seed.create_manual_planning_order_batch
```

The result prints the delivery date, company, source warehouse, and all 10 Sales Order names. Re-running it before planning is idempotent. After using batch `A`, generate another batch with `--kwargs '{"batch_code":"B","count":10}'`.

## Manual End-to-End Scenario

### 1. Create The Order

1. Sign into Neuradix POS as the sales rep.
2. Select a customer, add customer-priced items, and submit the order.
3. In Desk, open `Cassar Operations > Sales Orders` and confirm a submitted Sales Order exists with operations status `Order Confirmed`.

### 2. Plan The Route

1. Sign into the operations app as `transport.planner@neuradix.local`.
2. Open `Plans`, create a plan, and use the backend-provided default company and source warehouse.
3. Open the plan management screen and run `Preview Routes`.
4. Review route clusters and warnings, then apply the plan.
5. Select `Create Pick Lists` and confirm the linked Pick Lists are shown.

Insight performs plan selection, clustering, and route generation. The operations app does not duplicate those rules.

### 3. Pick

1. Sign in as `picker.one@neuradix.local`.
2. Open the available queue and claim one Pick List.
3. Scan each ERPNext item code or configured barcode until required quantities are complete.
4. Complete the Pick List. Repeating a scan request with the same event ID does not add stock twice.

### 4. Check

1. Sign in as `checker@neuradix.local`.
2. Open the checking queue and claim the completed Pick List.
3. Approve it, or reject it with a reason to return it to picking.
4. The configured separation rule prevents the picker from approving their own work.

### 5. Load And Dispatch

1. Sign in as `loader@neuradix.local`.
2. Open the loading queue, prepare the route manifest, and start loading.
3. Scan all manifest quantities and complete loading.
4. Confirm the route has an ERPNext Vehicle and Driver, then dispatch.
5. Verify the submitted stock transfer to the vehicle/transit warehouse, submitted Sales Invoice, and draft Delivery Note.

### 6. Deliver

1. Sign in as the driver assigned to the route.
2. Open the route and select a stop.
3. Choose `Delivered`, `Partially Delivered`, or `Failed`.
4. Enter the recipient, collect a signature, add notes, and optionally capture photo, GPS, collection amount, mode, and reference.
5. For partial delivery, enter actual delivered quantities. For failure, enter a failure reason.
6. Submit proof of delivery. The call is idempotent and can safely retry after a network interruption.

Expected accounting and stock results:

- Full: Delivery Note submitted; optional Payment Entry created for collection.
- Partial: Delivery Note submitted for delivered quantities; undelivered stock returned; credit note created.
- Failed: Draft Delivery Note removed; all stop stock returned; full credit note created.
- Final stop: Transport Route closes, then its Transport Plan closes when all related routes are terminal.

## Backend Inspection

Open `http://neuradix-cassar.localhost:8008/app/cassar-operations` as Administrator or the operations manager. The workspace links to:

- Sales Orders and Insight Transport Plans/Routes.
- ERPNext Pick Lists and immutable Fulfilment Events.
- Delivery Notes, Sales Invoices, Stock Entries, Payment Entries, and immutable Delivery Events.
- Driver, Vehicle, Item, Warehouse, and Cassar Operations Settings masters.

Document links use `custom_transport_route`, `custom_route_stop`, and `custom_ops_event_id`, making every dispatch/delivery transaction traceable back to its route and replay-safe mobile event.

## Automated Verification

Run the live site lifecycle, including real Insight planning and all delivery outcomes:

```bash
$BENCH_ROOT/run_bench_smoke.sh
```

Run every backend and Flutter regression suite from the single trigger:

```bash
$BENCH_ROOT/run_all_tests.sh
```

The smoke data is local/UAT-only and intentionally refuses to run on a production hostname.

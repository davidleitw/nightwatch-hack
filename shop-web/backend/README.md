# Backend

`shop-web/backend/` contains the FastAPI public gateway and the order service for 日日選物. The gateway keeps the existing public paths and validation schemas while forwarding product, cart, and checkout work to separate service owners. Full public fields and examples are in [../docs/API.md](../docs/API.md).

## Public API ownership

The public base URL remains `http://localhost:8000` for direct backend access or `http://localhost:8080` through the frontend proxy. `app.main:app` is the stateless gateway:

- `GET/POST/PUT/PATCH/DELETE /api/products...` forwards to `CATALOG_URL`.
- `/api/carts` and all cart item paths forward to `CART_URL`; cart checkout is handled by `ORDER_URL`.
- `POST /api/orders` and `/api/demo-faults` forward to `ORDER_URL`.
- `/api/health` checks the order service through its internal health endpoint.

The gateway validates the same public Pydantic models as the original API: product IDs are strict positive integers up to 2^63-1, prices are strict integers from 1 to 1,000,000,000, quantities are strict integers from 1 to 99, and product/cart/order request fields reject unknown fields. Downstream response status and JSON body are preserved. A downstream connection or timeout becomes HTTP 503; a downstream HTTP 4xx/5xx is relayed unchanged.

## Product and cart CRUD

The public product and anonymous cart CRUD contract is unchanged:

| Operation | Endpoint | Success |
| --- | --- | --- |
| List products | `GET /api/products` | 200, product array |
| Read product | `GET /api/products/{id}` | 200, product object |
| Create product | `POST /api/products` | 201, product object |
| Replace product | `PUT /api/products/{id}` | 200, product object |
| Patch product | `PATCH /api/products/{id}` | 200, product object |
| Delete product | `DELETE /api/products/{id}` | 204, no body |
| Create cart | `POST /api/carts` | 201, `{id, items, total}` |
| Read cart | `GET /api/carts/{cart_id}` | 200, cart |
| Delete cart | `DELETE /api/carts/{cart_id}` | 204, no body |
| Add or accumulate item | `POST /api/carts/{cart_id}/items` | 200, cart |
| Read items | `GET /api/carts/{cart_id}/items` | 200, item array |
| Set quantity | `PATCH /api/carts/{cart_id}/items/{product_id}` | 200, cart |
| Remove item | `DELETE /api/carts/{cart_id}/items/{product_id}` | 200, cart |
| Clear cart | `DELETE /api/carts/{cart_id}/items` | 200, empty cart |

Catalog and cart own separate SQLite databases. On first initialization each service may import its own tables from the read-only `LEGACY_DB_PATH` mount and records a migration version so a restart does not import or seed again. The service-specific database paths are configured by their environment; the order service defaults to `/data/order.db` and the common single-service fallback remains `/data/shop.db`.

## Checkout and durable operations

`POST /api/orders` keeps the request `{name, address, items}` and `POST /api/carts/{cart_id}/checkout` keeps `{name, address}`. Both return the existing `OrderResponse` with status 201 on success. The order service resolves current prices through catalog and stores the order snapshot in its own `orders` table.

An optional `Idempotency-Key` is forwarded by the gateway. Repeating the same key with the same request returns the original committed order without creating another order; reusing it for different checkout content returns 409. This applies to both checkout entry points, and the fingerprint includes the cart ID for cart checkout.

Cart checkout uses these order-owned operation states: `preparing`, `committed`, `done`, and `aborted`. The order service records an operation and attempt before calling the cart service. It then reserves a cart, looks up current catalog prices, and commits the order and `committed` operation in one order SQLite transaction. If the cart complete call fails after commit, the operation remains `committed`; a retry with the same key or the periodic reconciler completes the cart before marking it `done`. A pre-commit failure attempts the idempotent cart abort and records `aborted`.

The internal protocol is service-to-service only:

- Cart prepare: `POST /internal/carts/{cart_id}/prepare` with `{operation_id}` returns `{cart_id, operation_id, items}`. The same operation is idempotent; another operation holding the cart receives 400.
- Cart release: `POST /internal/carts/{cart_id}/complete` or `/abort` with `{operation_id}` returns 204 and is idempotent.
- Catalog lookup: `POST /internal/products/lookup` with `{product_ids}` returns `{products, missing_ids}`.

The order service runs SQLite work in its own database executor, while outgoing business requests use a separate standard-library HTTP executor. Control requests used by reconciliation and fault controls use a separate short-timeout executor, so a slow business write does not occupy the control path.

Checkout monitoring is shared through `app/monitoring.py`: the gateway records
`shop.checkout.request`, the order coordinator records `shop.checkout.logic`, and
checkout SQLite operations record `shop.db.write`. The order middleware's demo
delay is outside the logic timer but included in the gateway request timer.
The gateway supplies its own `X-Nightwatch-Parent-Invocation` header to order;
the DB executor copies the context so each SQL event keeps its logic parent.
HTTP 4xx are business rejections, HTTP 5xx are request failures, and successful
rollback is not a DB failure. Monitor placement, graph thresholds, and shared
JSONL delivery are documented in [Guard Room README](../../guardroom/README.md#結帳故障觀測).

## Demo fault controls

`/api/demo-faults` is implemented by the order service and proxied by the gateway. GET, POST, and DELETE share this response shape:

```json
{
  "cards": [
    {"fault_id": "checkout_exception", "title": "結帳例外", "description": "..."},
    {"fault_id": "database_write_lock", "title": "資料庫寫入鎖", "description": "..."},
    {"fault_id": "checkout_delay", "title": "結帳延遲", "description": "..."}
  ],
  "active": null,
  "lease_seconds": null,
  "delay_seconds": 10
}
```

Only one card may be active. Unknown IDs or extra fields return 422; an active, activating, or restoring card returns 409. DELETE is idempotent and wakes delayed checkouts. Faults remain active until a manual DELETE; there is no automatic lease or expiry, so `lease_seconds` is `null` and an active response keeps `expires_at` and `remaining_seconds` as `null` while reporting `started_at` and `status`. Process shutdown releases the database-lock connection and clears the in-memory fault. `checkout_exception` raises after real order and operation writes but before commit, so the order transaction rolls back. `database_write_lock` owns a dedicated SQLite connection and `BEGIN IMMEDIATE` for the order database. `checkout_delay` waits asynchronously for up to 10 seconds per checkout request and can be released by DELETE or process shutdown.

## Runtime configuration

The gateway reads:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CATALOG_URL` | `http://catalog:8000` | Catalog service URL |
| `CART_URL` | `http://cart:8000` | Cart service URL |
| `ORDER_URL` | `http://order:8000` | Order service URL |

The order service also reads `ORDER_DB_PATH` (falling back to `DB_PATH`, then `/data/order.db`) and `LEGACY_DB_PATH` (default `/legacy/shop.db`). Python dependencies remain the versions declared in `pyproject.toml` and `uv.lock`; the service-to-service client uses only the Python standard library.

## Application logging

The `shop` logger defaults to `LOG_LEVEL=INFO`; `LOG_DIR` defaults to `/logs`. Each application record goes to stdout and UTF-8 `app.log` with UTC ISO-8601 millisecond timestamp, level, logger name, and message. At UTC midnight, `app.log` rotates on the next record to `app.log.YYYY-MM-DD`, retaining seven backups.

Gateway, catalog, cart, and order request middleware record method, path without query, status, and duration in milliseconds. Unexpected exceptions are logged with traceback and raised again. Request bodies, names, addresses, and tokens are not logged. Logging initialization errors are printed and allowed to fail startup; the application logger does not reconfigure Uvicorn or the root logger globally.

## Local commands

From `shop-web/backend/`, the repository environment can be installed with the locked dependencies:

```sh
uv sync --frozen --no-dev
```

Production build, the ten existing integration tests, real HTTP service/fault recovery, checkout retries, and legacy row preservation have been verified. See `../docs/CHANGELOG.md` for exact results and limitations.

The frontend uses the cart CRUD and cart checkout APIs. Only the cart ID is kept in `localStorage`; legacy cart contents are imported before the old key is removed. Network errors and HTTP 5xx retain the original checkout payload and idempotency key for retry.

Cart reads return HTTP 503 when catalog lookup fails, preserving existing items. An abort received before prepare creates a durable aborted operation; a delayed prepare replays that cancellation without reserving the cart.

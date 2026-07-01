# Sales Miniapp Test Plan

This plan covers `sales-miniapp`, the customer-facing unified retail mini program, and
its `/api/sale-mini/` backend. The release goal is a Walmart-like buyer experience:
one public seller brand, product discovery across all internally listed consignment owners,
category browsing, cart, fulfillment-package checkout, WMS outbound creation, payment,
refund, and data-accurate inventory handling.

## Quality Gates

- A buyer can browse home, categories, product list, and product detail without logging in.
- Public catalog returns only active, listed `SaleProductConfig` rows, across all active internal owners.
- Customer pages must not expose merchant/store/owner discovery; there is one public seller brand.
- Home banners expose buyer-facing campaign entry points and can navigate to products, categories, searches, product lists, or internal pages.
- Product list supports keyword, category, brand, price, stock, and sort filters without leaking inactive/unlisted goods.
- Favorites and browse history keep `config_id` context so later cart repricing still uses the correct sale listing; reorder keeps the server-side fulfillment context from the original order.
- Hidden products, inactive products, inactive owners, and owner/product mismatches are not exposed.
- Buyer-facing miniapp pages must not show back-office terms such as WMS, PDA, old sales workbench, or procurement copy.
- Logged-in buyers can have bindings to multiple internal owners and can add products from those owners.
- WeChat login preserves all owner bindings for backend permission checks without exposing them as customer merchant choices.
- Login and profile APIs must not return top-level `owner` or `warehouse`; owner context stays inside internal `bindings` and fulfillment endpoints.
- Products from owners not yet opened for the buyer remain publicly browsable, while purchase actions show buyer-facing guidance.
- User center, benefits, address, order list, and after-sale list must not expose merchant/owner switches.
- Buyer-visible product, cart checkout, address, and address-edit URLs must not carry `owner_id`; frontend keeps listing or fulfillment context through `config_id`, `cart_id`, or temporary local storage.
- Public browse APIs for home, products, categories, and brands must not return `owner_id`, `owner`, `owner_name`, `code`, `sku`, `barcodes`, or internal base-price fields; authenticated cart, order, address, coupon, and payment APIs may keep owner/product-code context for fulfillment and data accuracy.
- Public product search must match retail-facing terms such as product name, spec, category name, or brand name, not internal product codes, SKUs, barcodes, or brand/category codes.
- Public product detail ignores owner browse filters; `config_id` is the only public listing context.
- Cart data is persisted on the server and internally grouped by owner as fulfillment packages.
- A mixed-owner checkout is submitted once by the buyer and split by the backend into one WMS outbound order per owner/customer binding.
- Multi-package orders are grouped back into one buyer-visible order row and detail response.
- Multi-package checkout currently uses offline/platform-confirmed payment and does not expose unavailable aggregate WeChat pay, coupon, or point redemption.
- Order preview and order creation recalculate price, unit conversion, stock, coupon, points, and payable amount server-side.
- WMS `OutboundOrder` and `OutboundOrderLine` remain the fulfillment source of truth.
- Miniapp never directly posts inventory deduction.
- Payment callback, refund, cancel, and unpaid-expiry handling are idempotent and release or confirm related resources correctly.
- Coupon, points, payment, refund, and outbound-order amounts reconcile back to the recorded sale-mini order mapping.
- Listed sale configs, cart items, order mappings, payments, refunds, coupons, adjustments, and inventory summaries pass the data-accuracy validator.
- Buyer-facing order tabs and status names use mall language such as `待付款` and `待发货`, not WMS audit/picking language.
- Order detail shows buyer-readable fulfillment progress instead of internal warehouse statuses.
- Frontend route structure is a mall, not the old sales workbench.
- `wmspda` is not modified by this work.

## Automated Test Matrix

| Area | Evidence | Command |
| --- | --- | --- |
| One-command quality gate, no DB | Mall structure, Django check, pure unit tests, H5/WeChat builds | `cd sales-miniapp && npm run test:quality` |
| One-command quality gate, faster local loop | Same as above, but skips H5/WeChat builds | `cd sales-miniapp && npm run test:quality -- --skip-build` |
| One-command quality gate, full DB | Adds sale-mini API DB tests, console catalog-management DB tests, and live data validator using normal migrations | `cd sales-miniapp && npm run test:quality -- --db` |
| One-command quality gate, fast DB | Adds sale-mini API DB tests, console catalog-management DB tests, and live data validator with pytest-django `--no-migrations` | `cd sales-miniapp && npm run test:quality -- --skip-build --db --fast-db` |
| Django config sanity | Settings, URL imports, model checks load | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py check` |
| Migration drift | Model changes have migrations | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py makemigrations --check --dry-run salesapp` |
| Sale-mini pure unit tests | Status mapping, quantity rules, pricing helper behavior, validator rounding/sample counting | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python -m pytest -q allapp/salesapp/test_salemini_unit.py allapp/salesapp/test_mobile_api_unit.py allapp/salesapp/test_services_pricing_unit.py` |
| Sale-mini API and data accuracy | Public catalog, brand filters, internal owner filters, multi-owner cart, unified checkout with backend split, outbound creation, payments, refunds, expiry | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python -m pytest -q --reuse-db --disable-warnings allapp/salesapp/tests.py::SaleMiniApiTests` |
| Sale-mini API fast local run | Same API business assertions without replaying migrations | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python -m pytest -q --reuse-db --no-migrations --disable-warnings allapp/salesapp/tests.py::SaleMiniApiTests` |
| Sale-mini API with named MySQL test DB | Same API coverage, using an explicitly configured test schema | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost DB_TEST_NAME=<CLEAN_TEST_DB> .venv/bin/python -m pytest -q --reuse-db --disable-warnings allapp/salesapp/tests.py::SaleMiniApiTests` |
| Sale-mini console catalog management | `/console/sale-mini/products/` listing filters, bulk config creation, listing/unlisting, price/badge updates, public catalog visibility, and inventory non-mutation | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python -m pytest -q --reuse-db --disable-warnings allapp/console/tests.py` |
| Sale-mini live data validator | Read-only invariant scan for sale config ownership, server cart, order amounts, payments/refunds, coupons/points, and non-negative available inventory | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py validate_sale_mini_data_accuracy --fail-on-issues --limit 20` |
| Frontend JSON | `pages.json` and `manifest.json` are valid | `node -e "JSON.parse(require('fs').readFileSync('sales-miniapp/pages.json','utf8')); JSON.parse(require('fs').readFileSync('sales-miniapp/manifest.json','utf8')); console.log('json ok')"` |
| Mall route shape | Old workbench pages are not registered in `pages.json`; tab bar has mall tabs | inspect `sales-miniapp/pages.json` |
| Mall structure regression | Unified retail pages, no merchant/store route exposure, tab navigation, internal owner cart/checkout, and old workbench route exclusions remain intact | `cd sales-miniapp && npm run test:structure` |
| H5 build | Uni-app H5 compiles | `cd sales-miniapp && npm run build:h5` |
| WeChat mini program build | Uni-app WeChat mini program compiles | `cd sales-miniapp && npm run build:mp-weixin` |

## Manual VM / HBuilderX Acceptance

Run these after applying migrations and listing at least two owners' products.

1. Backend data setup:
   - `python manage.py migrate`
   - `python manage.py bootstrap_sale_mini_catalog --limit 20`
   - `python manage.py bootstrap_sale_mini_catalog --owner-code <OWNER_CODE> --apply --listed`
   - Or open `/console/sale-mini/products/`, filter by owner/category/stock/price, select saleable goods, then run `创建商城配置` and `上架`.
2. H5 smoke:
   - Open HBuilderX H5 URL.
   - Confirm home page loads without login.
   - Confirm categories include `全部`.
   - Search a listed product by name, brand, or retail keyword.
   - Filter products by category, brand, price, stock, and sort order.
   - Open detail, add to cart, then log in when prompted.
   - Favorite a product, reopen it from 我的收藏, and confirm the same listing context is preserved.
   - Open 浏览足迹 and confirm recently viewed products still navigate to the correct product detail.
3. Internal multi-owner consignment smoke:
   - List products for two different owners.
   - Add one product from each owner.
   - Cart shows two delivery packages.
   - Global checkout opens one confirm page.
   - Confirm page explains multi-package orders use platform-confirmed payment and temporarily disables aggregate WeChat pay/coupon/points.
   - Submit creates one buyer-visible order and multiple backend WMS outbound orders.
4. Order data accuracy:
   - Preview total equals server-calculated line amounts.
   - Change backend sale price, refresh cart, cart reprices from server.
   - Set quantity above available stock, preview/order rejects with stock error.
   - Successful order creates `OutboundOrder` and allocation/task state through WMS rules.
5. Payment and refund smoke:
   - WeChat prepay returns miniapp pay params when payment config is present.
   - Callback is idempotent: duplicate callbacks do not double-confirm resources.
   - Refund request only works for a paid order and creates refund state once.
6. Security and scope:
   - Public catalog does not return unlisted products.
   - A buyer without a binding cannot add another owner product.
   - Order detail/cancel/payment/refund only work for the buyer's own bindings.
7. Frontend route smoke:
   - Bottom tabs: 首页, 分类, 购物车, 订单, 我的.
   - Login success returns to 首页 tab.
   - 未登录进入 我的 redirects to login.
   - Order submit opens 下单结果, not a raw detail page.
   - 下单结果 supports wait-pay, paid, and offline-payment states with buyer-facing actions.
   - Cart and order pages do not use old sales workbench routes.
8. Reorder smoke:
   - Open a completed or cancelled order.
   - Tap `再来一单`.
   - Confirm cart is filled with the original goods under the original delivery package.
   - Preview recalculates current price, stock, coupon, and points instead of trusting stale order-line amounts.

## Current Run Record

Fill this section after each execution.

| Check | Result | Notes |
| --- | --- | --- |
| Django check | passed | `System check identified no issues (0 silenced).` |
| Migration drift | passed | `No changes detected in app 'salesapp'`; sandbox emitted a MySQL socket warning while checking migration history. |
| Pure unit tests | passed | `15 passed`; includes validator rounding/sample-count coverage. Warnings are existing Django 6.0 deprecation warnings for `CheckConstraint.check`. |
| Sale-mini data validator | passed | `validate_sale_mini_data_accuracy --fail-on-issues --limit 5 --json` returned `ok=true`, `issue_count=0` against the real MySQL connection. |
| One-command quality gate | passed | `npm run test:quality -- --skip-build`, `npm run test:quality -- --skip-build --data-accuracy`, `npm run test:quality`, and `npm run test:quality -- --skip-build --db --fast-db` passed. The full non-DB gate includes H5 and WeChat builds. The fast DB gate includes API DB tests and live data validation. |
| Sale-mini API tests | passed in fast DB mode | Full API business suite passed with `--reuse-db --no-migrations`: `41 passed`, covering catalog scope, multi-owner cart, unified checkout with backend split, buyer-visible combined orders, cancel inventory release, outbound creation/allocation, payment callbacks, refunds, after-sale, and unpaid expiry release. |
| Frontend JSON | passed | `pages.json` and `manifest.json` parse successfully. |
| Mall route shape | passed | Old workbench pages are not registered in `pages.json`; tab bar is `首页 / 分类 / 购物车 / 订单 / 我的`. |
| Mall structure regression | passed | `npm run test:structure` passed and checks tab routes, unified retail pages, absence of merchant/store routes, category `全部`, product-list filters, internal owner bindings, product-detail purchase context, order fulfillment progress, buyer-facing error/copy guard, unified multi-package checkout, combined-order payment notice, tab navigation APIs, and absence of old sales workbench files/API wrappers. |
| H5 build | passed | `npm run build:h5` completed. Uni compiler printed a uniCloud hosting advertisement; this does not mean the project uses uniCloud. |
| WeChat build | passed | `npm run build:mp-weixin` completed. Sass legacy API warning is from the toolchain. |

## API Test DB Notes

The API/data-accuracy test layer has been verified in fast DB mode against MySQL.
The local MySQL migration-mode setup is much slower than the application tests
themselves: the first clean fast-DB smoke case needed `674.25s` just to initialize
the test schema, while the full reused API suite then completed in `188.18s`.

For normal migration-mode verification, use a clean isolated Django test database:

1. Rebuild the isolated Django test DB, then rerun:

   ```bash
   SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost \
     .venv/bin/python -m pytest -q --create-db --disable-warnings \
     allapp/salesapp/tests.py::SaleMiniApiTests
   ```

2. Or create a clean MySQL schema in advance and pass it through `DB_TEST_NAME`:

   ```bash
   SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost \
     DB_TEST_NAME=<CLEAN_TEST_DB> \
     .venv/bin/python -m pytest -q --reuse-db --disable-warnings \
     allapp/salesapp/tests.py::SaleMiniApiTests
   ```

3. For local business regression without replaying migrations, use the verified fast DB command:

   ```bash
   cd sales-miniapp && npm run test:quality -- --skip-build --db --fast-db
   ```

SQLite was considered as a local fallback, but this Python runtime does not include
the `_sqlite3` extension, so SQLite cannot run in the current environment.

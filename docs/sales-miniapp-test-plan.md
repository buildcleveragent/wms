# Sales Miniapp Test Plan

This plan covers `sales-miniapp`, the customer-facing marketplace mini program, and
its `/api/sale-mini/` backend. The release goal is a normal buyer mall experience:
public product discovery across all listed owners, category browsing, cart, per-merchant
checkout, WMS outbound creation, payment, refund, and data-accurate inventory handling.

## Quality Gates

- A buyer can browse home, categories, product list, and product detail without logging in.
- Public catalog returns only active, listed `SaleProductConfig` rows, across all active owners.
- Home API returns merchant discovery data so buyers can enter an owner's product list directly.
- Home banners expose buyer-facing campaign entry points and can navigate to products, categories, merchants, searches, product lists, or internal pages.
- Home `merchants` and `/api/sale-mini/merchants/` use the same active listed goods scope.
- Merchant filters return only owners with active listed goods; product list and category pages both respect `owner_id`.
- Product list supports keyword, category, merchant, brand, price, stock, and sort filters without leaking inactive/unlisted goods.
- Favorites, browse history, and reorder keep `owner_id` and `config_id` context so later cart repricing still uses the correct sale listing.
- Hidden products, inactive products, inactive owners, and owner/product mismatches are not exposed.
- Buyer-facing miniapp pages must not show back-office terms such as WMS, PDA, old sales workbench, or procurement copy.
- Logged-in buyers can have bindings to multiple owners and can add products from those owners.
- WeChat login preserves all owner bindings on the frontend, so a multi-merchant buyer is not reduced to a single-owner session.
- Products from merchants not yet opened for the buyer remain publicly browsable, while purchase actions show buyer-facing guidance instead of back-office permission wording.
- User-center points/coupon summary and the benefits page both stay scoped to the buyer-selected merchant.
- Address book can switch between the buyer's opened merchants, while checkout address selection remains locked to the current merchant.
- Order list and after-sale list can show all merchants or filter by one opened merchant.
- Cart data is persisted on the server and grouped by owner.
- A checkout can only create one WMS outbound order for one owner/customer binding.
- Mixed-owner checkout is rejected with a clear split-checkout message.
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
| One-command quality gate, full DB | Adds sale-mini API DB tests and live data validator using normal migrations | `cd sales-miniapp && npm run test:quality -- --db` |
| One-command quality gate, fast DB | Adds sale-mini API DB tests with pytest-django `--no-migrations` plus live data validator | `cd sales-miniapp && npm run test:quality -- --skip-build --db --fast-db` |
| Django config sanity | Settings, URL imports, model checks load | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py check` |
| Migration drift | Model changes have migrations | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py makemigrations --check --dry-run salesapp` |
| Sale-mini pure unit tests | Status mapping, quantity rules, pricing helper behavior, validator rounding/sample counting | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python -m pytest -q allapp/salesapp/test_salemini_unit.py allapp/salesapp/test_mobile_api_unit.py allapp/salesapp/test_services_pricing_unit.py` |
| Sale-mini API and data accuracy | Public catalog, merchant list, brand filters, owner filters, multi-owner cart, split checkout, outbound creation, payments, refunds, expiry | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python -m pytest -q --reuse-db --disable-warnings allapp/salesapp/tests.py::SaleMiniApiTests` |
| Sale-mini API fast local run | Same API business assertions without replaying migrations | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python -m pytest -q --reuse-db --no-migrations --disable-warnings allapp/salesapp/tests.py::SaleMiniApiTests` |
| Sale-mini API with named MySQL test DB | Same API coverage, using an explicitly configured test schema | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost DB_TEST_NAME=<CLEAN_TEST_DB> .venv/bin/python -m pytest -q --reuse-db --disable-warnings allapp/salesapp/tests.py::SaleMiniApiTests` |
| Sale-mini live data validator | Read-only invariant scan for sale config ownership, server cart, order amounts, payments/refunds, coupons/points, and non-negative available inventory | `SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py validate_sale_mini_data_accuracy --fail-on-issues --limit 20` |
| Frontend JSON | `pages.json` and `manifest.json` are valid | `node -e "JSON.parse(require('fs').readFileSync('sales-miniapp/pages.json','utf8')); JSON.parse(require('fs').readFileSync('sales-miniapp/manifest.json','utf8')); console.log('json ok')"` |
| Mall route shape | Old workbench pages are not registered in `pages.json`; tab bar has mall tabs | inspect `sales-miniapp/pages.json` |
| Mall structure regression | Public catalog/merchant services, tab navigation, per-owner cart/checkout, and old workbench route exclusions remain intact | `cd sales-miniapp && npm run test:structure` |
| H5 build | Uni-app H5 compiles | `cd sales-miniapp && npm run build:h5` |
| WeChat mini program build | Uni-app WeChat mini program compiles | `cd sales-miniapp && npm run build:mp-weixin` |

## Manual VM / HBuilderX Acceptance

Run these after applying migrations and listing at least two owners' products.

1. Backend data setup:
   - `python manage.py migrate`
   - `python manage.py bootstrap_sale_mini_catalog --limit 20`
   - `python manage.py bootstrap_sale_mini_catalog --owner-code <OWNER_CODE> --apply --listed`
2. H5 smoke:
   - Open HBuilderX H5 URL.
   - Confirm home page loads without login.
   - Confirm categories include `全部`.
   - Search a listed product by name/code/barcode.
   - Filter products by merchant, category, brand, price, stock, and sort order.
   - Open detail, add to cart, then log in when prompted.
   - Favorite a product, reopen it from 我的收藏, and confirm the same merchant/listing context is preserved.
   - Open 浏览足迹 and confirm recently viewed products still navigate to the correct product detail.
3. Multi-owner mall smoke:
   - List products for two different owners.
   - Add one product from each owner.
   - Cart shows two merchant groups.
   - Global checkout refuses mixed-owner checkout.
   - Merchant-group checkout opens confirm page for that owner only.
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
   - Cart and order pages do not use old sales workbench routes.
8. Reorder smoke:
   - Open a completed or cancelled order.
   - Tap `再来一单`.
   - Confirm cart is filled with the original goods under the original merchant group.
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
| Sale-mini API tests | passed in fast DB mode | The dirty `test_wms_db` was explicitly rebuilt. One smoke case then passed after initial table creation: `1 passed` in `674.25s`. Full API business suite passed with `--reuse-db --no-migrations`: `35 passed` in `181.75s`, covering catalog scope, multi-owner cart, split checkout, outbound creation/allocation, payment callbacks, refunds, after-sale, and unpaid expiry release. Standard migration-mode `--db` is still too slow for this local MySQL run and previously failed on the dirty reused test DB before business assertions. |
| Frontend JSON | passed | `pages.json` and `manifest.json` parse successfully. |
| Mall route shape | passed | Old workbench pages are not registered in `pages.json`; tab bar is `首页 / 分类 / 购物车 / 订单 / 我的`. |
| Mall structure regression | passed | `npm run test:structure` passed and checks tab routes, public catalog/merchant services, home banner navigation, home merchant discovery, category `全部`, product-list and category merchant filters, WeChat multi-owner bindings, product-detail purchase permission notice, user-center benefit merchant switch, multi-merchant address switch, order/after-sale merchant filters, order fulfillment progress, buyer-facing error/copy guard, owner-scoped checkout, split-checkout message, tab navigation APIs, and absence of old sales workbench files/API wrappers. |
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

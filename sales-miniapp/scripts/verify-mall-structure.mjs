import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

const root = process.cwd()

function readText(file) {
  return fs.readFileSync(path.join(root, file), 'utf8')
}

function readJson(file) {
  return JSON.parse(readText(file))
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message)
  }
}

function assertIncludes(text, needle, label) {
  assert(text.includes(needle), `${label} must include ${needle}`)
}

function assertNotIncludes(text, needle, label) {
  assert(!text.includes(needle), `${label} must not include ${needle}`)
}

function assertFileMissing(file, label) {
  assert(!fs.existsSync(path.join(root, file)), `${label} must not exist: ${file}`)
}

function assertPngIcon(file, label) {
  const fullPath = path.join(root, file)
  assert(fs.existsSync(fullPath), `${label} must exist: ${file}`)
  const bytes = fs.readFileSync(fullPath)
  assert(bytes.length > 24, `${label} must not be empty: ${file}`)
  assert(bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47, `${label} must be a PNG: ${file}`)
  const width = bytes.readUInt32BE(16)
  const height = bytes.readUInt32BE(20)
  assert(width === 81 && height === 81, `${label} must be 81x81px: ${file}`)
}

const pagesJson = readJson('pages.json')
const manifest = readJson('manifest.json')
const registeredPages = pagesJson.pages.map((item) => item.path)

const expectedTabPages = [
  'pages/index/index',
  'pages/category/category',
  'pages/cart/cart',
  'pages/order-list/order-list',
  'pages/user/user',
]
const expectedTabTexts = ['首页', '分类', '购物车', '订单', '我的']
const expectedTabIcons = ['home', 'category', 'cart', 'order', 'user']
const expectedMallPages = [
  'pages/order-result/order-result',
  'pages/benefits/benefits',
  'pages/after-sales/after-sales',
  'pages/favorites/favorites',
  'pages/history/history',
  'pages/address/address',
  'pages/address-edit/address-edit',
  'pages/login/login',
]
const forbiddenCustomerRoutes = [
  'pages/merchants/merchants',
  'pages/merchant-detail/merchant-detail',
]
const staleWorkbenchPages = [
  'pages/home/home',
  'pages/customers/select',
  'pages/catalog/index',
  'pages/cart/index',
  'pages/orders/list',
  'pages/orders/detail',
]

assert(registeredPages[0] === 'pages/index/index', 'home page must be the first registered page')
for (const page of expectedTabPages) {
  assert(registeredPages.includes(page), `registered pages must include ${page}`)
}
for (const page of expectedMallPages) {
  assert(registeredPages.includes(page), `registered mall pages must include ${page}`)
}
for (const page of [...forbiddenCustomerRoutes, ...staleWorkbenchPages]) {
  assert(!registeredPages.includes(page), `customer-facing route must not be registered: ${page}`)
  assertFileMissing(`${page}.vue`, 'customer-facing route file')
}
assertFileMissing('stores/salesCart.js', 'old sales workbench store')

const buyerPageForbiddenCopy = [
  'WMS',
  'PDA',
  '销售员',
  '客户采购小程序',
  '仓库商城',
  '推荐采购',
  '出库流程',
  'merchant',
  'MERCHANT',
  '货主',
  '全部商家',
  '精选商家',
  '商家店铺',
  '查看该商家',
  '该商家暂未',
  '多商家',
  '同店',
  '进店',
]
for (const page of pagesJson.pages) {
  const text = readText(`${page.path}.vue`)
  for (const phrase of buyerPageForbiddenCopy) {
    assertNotIncludes(text, phrase, `${page.path}.vue buyer-facing copy`)
  }
}

const tabItems = (pagesJson.tabBar && pagesJson.tabBar.list) || []
assert(tabItems.length === expectedTabPages.length, 'tabBar must have five mall tabs')
for (const [index, page] of expectedTabPages.entries()) {
  assert(tabItems[index].pagePath === page, `tab ${index} page must be ${page}`)
  assert(tabItems[index].text === expectedTabTexts[index], `tab ${index} text must be ${expectedTabTexts[index]}`)
  const icon = expectedTabIcons[index]
  const iconPath = `static/tabbar/${icon}-normal.png`
  const selectedIconPath = `static/tabbar/${icon}-active.png`
  assert(tabItems[index].iconPath === iconPath, `tab ${index} iconPath must be ${iconPath}`)
  assert(tabItems[index].selectedIconPath === selectedIconPath, `tab ${index} selectedIconPath must be ${selectedIconPath}`)
  assertPngIcon(iconPath, `tab ${index} icon`)
  assertPngIcon(selectedIconPath, `tab ${index} selected icon`)
}

assert(manifest.vueVersion === '3', 'sales miniapp must stay on Vue 3 uni-app')
assert(!Object.prototype.hasOwnProperty.call(manifest, 'uniCloud'), 'sales miniapp must not configure uniCloud')
assert(manifest.name === '博悦商城', 'manifest app name must be buyer-facing')
assert(manifest.description === '博悦商城商品选购小程序', 'manifest description must be buyer-facing')
assertNotIncludes(manifest.description, '多商家', 'manifest description')
assert(manifest.h5 && manifest.h5.title === '博悦商城', 'h5 title must be buyer-facing')
assert(pagesJson.globalStyle.navigationBarTitleText === '博悦商城', 'global title must be buyer-facing')
assert(pagesJson.pages[0].style.navigationBarTitleText === '博悦商城', 'home title must be buyer-facing')

const request = readText('utils/request.js')
assertIncludes(request, '/api/sale-mini/home/', 'request API')
assertIncludes(request, '/api/sale-mini/categories/', 'request API')
assertIncludes(request, '/api/sale-mini/brands/', 'request API')
assertIncludes(request, '/api/sale-mini/coupons/', 'request API')
assertIncludes(request, '/api/sale-mini/points/', 'request API')
assertIncludes(request, '/api/sale-mini/after-sales/', 'request API')
assertIncludes(request, '/api/sale-mini/products/', 'request API')
assertIncludes(request, 'authRedirect: false', 'profile request')
assertIncludes(request, '当前商品暂未对你的账号开通购买权限', 'buyer-facing error copy')
assertIncludes(request, '购物车已按配送包裹拆分，可统一提交。', 'buyer-facing combined checkout copy')
assertNotIncludes(request, 'saleMiniMerchants', 'request API must not expose merchant list')
assertNotIncludes(request, '/api/sale-mini/merchants/', 'request API must not expose merchant list')
assertNotIncludes(request, '/api/sales/mobile/', 'request API')
assertNotIncludes(request, 'createOrder', 'request API')
assertNotIncludes(request, 'quoteOrder', 'request API')
assertNotIncludes(request, 'submitOrder', 'request API')
assertNotIncludes(request, '未绑定出库仓库', 'request must not retain back-office warehouse copy')

const productService = readText('services/product.js')
assertIncludes(productService, 'api.saleMiniHome', 'product service')
assertIncludes(productService, 'api.saleMiniCategories', 'product service')
assertIncludes(productService, 'api.saleMiniBrands', 'product service')
assertIncludes(productService, 'api.saleMiniProducts', 'product service')
assertNotIncludes(productService, 'saleMiniMerchants', 'product service')
assertNotIncludes(productService, 'sales/mobile', 'product service')

const cartStore = readText('stores/cart.js')
assertIncludes(cartStore, 'config_id: product.config_id', 'cart add product config context')
assertIncludes(cartStore, 'cart_ids: cartIds', 'cart checkout must submit source cart ids for combined packages')
assertIncludes(cartStore, 'owner_id: line.owner_id', 'cart server owner grouping context')
assertNotIncludes(cartStore, 'owner_id: product.owner_id', 'cart add product must not depend on public product owner id')
assertNotIncludes(cartStore, 'owner_name', 'cart store must not keep buyer-facing owner names')

const browseStore = readText('stores/browse.js')
assertIncludes(browseStore, 'sale_mini_favorite_products', 'browse store favorites')
assertIncludes(browseStore, 'sale_mini_recent_products', 'browse store recent history')
assertIncludes(browseStore, 'config_id: product.config_id', 'browse store config context')
assertNotIncludes(browseStore, 'owner_id: product.owner_id', 'browse store must not persist public product owner id')
assertNotIncludes(browseStore, 'owner_name', 'browse store must not keep buyer-facing owner names')
assertNotIncludes(browseStore, 'product.code || product.product_code', 'browse store must not persist inventory product codes')

const productCard = readText('components/ProductCard.vue')
assertIncludes(productCard, 'stockLabel', 'product card retail stock badge')
assertIncludes(productCard, 'addText', 'product card retail add button')
assertIncludes(productCard, '加购', 'product card retail add button copy')
assertIncludes(productCard, '缺货', 'product card retail out-of-stock copy')
assertIncludes(productCard, 'subtitle', 'product card retail subtitle')
assertNotIncludes(productCard, 'product.code', 'product card must not foreground inventory code')
assertNotIncludes(productCard, 'owner_name', 'product card must not show owner name')
assertNotIncludes(productCard, 'merchant', 'product card must not use merchant wording')

const indexPage = readText('pages/index/index.vue')
assertIncludes(indexPage, 'productService.home()', 'home page')
assertIncludes(indexPage, '@click="openBanner(banner)"', 'home banner navigation')
assertIncludes(indexPage, "const profileName = computed(() => '博悦商城')", 'home unified retail brand')
assertIncludes(indexPage, 'brandTagline', 'home retail tagline naming')
assertNotIncludes(indexPage, 'warehouseName', 'home must not use warehouse naming for retail tagline')
assertIncludes(indexPage, '搜索商品、品牌、关键词', 'home retail search placeholder')
assertNotIncludes(indexPage, '搜索商品名称、编码、条码', 'home must not foreground inventory identifiers')
assertNotIncludes(indexPage, 'return `${current.customer.name} 默认收货信息`', 'home must not use customer name as storefront')
assertIncludes(indexPage, '品质优选', 'home unified retail channel')
assertIncludes(indexPage, '有货商品', 'home stock entry')
assertIncludes(indexPage, 'only_stock=1', 'home stock navigation')
assertIncludes(indexPage, '配送到', 'home fulfillment entry')
assertIncludes(indexPage, '门店自提', 'home fulfillment entry')
assertIncludes(indexPage, '领券中心', 'home benefit entry')
assertIncludes(indexPage, '会员积分', 'home benefit entry')
assertIncludes(indexPage, '售后服务', 'home after-sale entry')
assertIncludes(indexPage, '/pages/benefits/benefits', 'home benefit navigation')
assertIncludes(indexPage, '/pages/after-sales/after-sales', 'home after-sale navigation')
assertIncludes(indexPage, 'config_id=${product.config_id}', 'home product detail context')
assertIncludes(indexPage, "uni.switchTab({ url: '/pages/category/category' })", 'home category navigation')
assertIncludes(indexPage, "uni.switchTab({ url: '/pages/cart/cart' })", 'home cart navigation')
assertIncludes(indexPage, "uni.switchTab({ url: '/pages/user/user' })", 'home user navigation')
assertNotIncludes(indexPage, 'productService.merchants', 'home must not expose merchant list')
assertNotIncludes(indexPage, "['MERCHANT', 'OWNER', 'STORE']", 'home banner must not expose owner storefront routing')
assertNotIncludes(indexPage, 'product-list/product-list?owner_id=', 'home must not expose owner storefront routing')
assertNotIncludes(indexPage, 'owner_id=${banner.owner_id}', 'home product banner must not expose owner id in URL')
assertNotIncludes(indexPage, '/pages/merchants/merchants', 'home must not expose merchant route')
assertNotIncludes(indexPage, '/pages/merchant-detail/merchant-detail', 'home must not expose merchant detail route')

const loginPage = readText('pages/login/login.vue')
assertIncludes(loginPage, '博悦商城', 'login buyer-facing copy')
assertIncludes(loginPage, '统一商城在线选购', 'login buyer-facing copy')
assertNotIncludes(loginPage, '客户采购小程序', 'login buyer-facing copy')

const userPage = readText('pages/user/user.vue')
assertIncludes(userPage, '会员账户 · 统一商城服务', 'user unified retail copy')
assertIncludes(userPage, 'benefitService.coupons()', 'user aggregate coupons')
assertIncludes(userPage, 'benefitService.points()', 'user aggregate points')
assertIncludes(userPage, '优惠券与积分', 'user benefit entry')
assertIncludes(userPage, '我的收藏', 'user favorites entry')
assertIncludes(userPage, '/pages/favorites/favorites', 'user favorites navigation')
assertIncludes(userPage, '浏览足迹', 'user history entry')
assertIncludes(userPage, '/pages/history/history', 'user history navigation')
assertIncludes(userPage, '全部商品', 'user product list entry')
assertIncludes(userPage, '/pages/product-list/product-list', 'user product list navigation')
assertIncludes(userPage, '售后服务', 'user after-sale entry')
assertIncludes(userPage, '待付款', 'user order shortcuts')
assertIncludes(userPage, '待发货', 'user order shortcuts')
assertIncludes(userPage, 'sale_mini_pending_order_status', 'user order shortcuts')
assertNotIncludes(userPage, '.map((item) => item.owner)', 'user must not expose owner context selection')
assertNotIncludes(userPage, 'current.owner.name', 'user must not use owner name as profile name')
assertNotIncludes(userPage, 'current.customer.name', 'user must not use back-office customer name as profile name')
assertNotIncludes(userPage, '/pages/merchants/merchants', 'user must not expose merchant route')

const sessionStore = readText('stores/session.js')
assertIncludes(sessionStore, 'bindings: data.bindings || []', 'wechat login keeps internal owner bindings')
assertNotIncludes(sessionStore, 'owner: data.owner', 'wechat login profile must not expose top-level owner')
assertNotIncludes(sessionStore, 'warehouse: data.warehouse', 'wechat login profile must not expose top-level warehouse')

const categoryPage = readText('pages/category/category.vue')
assertIncludes(categoryPage, "const ALL_CATEGORY_ID = 'all'", 'category page')
assertIncludes(categoryPage, "name: '全部'", 'category page')
assertIncludes(categoryPage, 'productService.categories()', 'category page global categories')
assertIncludes(categoryPage, 'productService.list', 'category page')
assertIncludes(categoryPage, 'config_id=${product.config_id}', 'category product detail context')
assertNotIncludes(categoryPage, 'productService.merchants', 'category must not expose merchant filter')
assertNotIncludes(categoryPage, 'query.owner_id', 'category page must not accept owner storefront filtering')
assertNotIncludes(categoryPage, 'owner_id: ownerId.value', 'category page must not owner-filter products')
assertNotIncludes(categoryPage, 'owner_id=${ownerId.value}', 'category page must not propagate owner storefront filtering')

const productListPage = readText('pages/product-list/product-list.vue')
assertIncludes(productListPage, 'placeholder="商品、品牌、关键词"', 'product list retail search placeholder')
assertNotIncludes(productListPage, '商品名称、编码、条码', 'product list must not foreground inventory identifiers')
assertIncludes(productListPage, 'productService.brands', 'product list brand filter')
assertIncludes(productListPage, 'brand_id: brandId.value', 'product list brand filter')
assertIncludes(productListPage, '全部品牌', 'product list brand filter')
assertIncludes(productListPage, 'selectBrand', 'product list brand filter')
assertIncludes(productListPage, 'config_id=${product.config_id}', 'product list detail context')
assertIncludes(productListPage, 'price_desc', 'product list price sort')
assertIncludes(productListPage, "query.only_stock === '1'", 'product list stock query')
assertIncludes(productListPage, '搜索历史', 'product list search history')
assertIncludes(productListPage, '热门搜索', 'product list hot keywords')
assertIncludes(productListPage, 'sale_mini_search_history', 'product list search history storage')
assertNotIncludes(productListPage, 'productService.merchants', 'product list must not expose merchant filter')
assertNotIncludes(productListPage, 'query.owner_id', 'product list must not accept owner storefront filtering')
assertNotIncludes(productListPage, 'owner_id: ownerId.value', 'product list must not owner-filter products')

const productDetailPage = readText('pages/product-detail/product-detail.vue')
assertIncludes(productDetailPage, '相关推荐', 'product detail recommendations')
assertIncludes(productDetailPage, 'favoriteText', 'product detail favorite state')
assertIncludes(productDetailPage, 'toggleFavorite', 'product detail favorite action')
assertIncludes(productDetailPage, 'browse.addRecent(product.value)', 'product detail recent history')
assertIncludes(productDetailPage, 'productService.detail(id, params)', 'product detail query context')
assertIncludes(productDetailPage, 'query.config_id', 'product detail query config context')
assertIncludes(productDetailPage, 'productDetailParams', 'product detail related product context')
assertIncludes(productDetailPage, 'loadRelatedProducts', 'product detail related products')
assertIncludes(productDetailPage, 'params.category_id = product.value.category_id', 'product detail related products should be category/global')
assertIncludes(productDetailPage, 'productSubtitle', 'product detail retail subtitle')
assertIncludes(productDetailPage, '规格与服务', 'product detail retail service copy')
assertIncludes(productDetailPage, '起购', 'product detail retail purchase rule copy')
assertNotIncludes(productDetailPage, 'purchaseNotice', 'product detail must not expose per-owner purchase gate')
assertNotIncludes(productDetailPage, 'canPurchaseCurrentOwner', 'product detail must not gate buying by owner binding')
assertNotIncludes(productDetailPage, 'session.fetchProfile()', 'product detail must not fetch owner bindings before buying')
assertNotIncludes(productDetailPage, '当前商品暂未对你的账号开通购买权限', 'product detail must not show owner permission copy')
assertNotIncludes(productDetailPage, '包装要求', 'product detail must not use warehouse packaging wording')
assertNotIncludes(productDetailPage, '<text>规则</text>', 'product detail must not use internal rule label')
assertIncludes(productDetailPage, 'ProductCard', 'product detail related product card')
assertNotIncludes(productDetailPage, 'owner_id: product.value.owner_id', 'product detail related products must not be owner-scoped')
assertNotIncludes(productDetailPage, 'query.owner_id', 'product detail must not read owner id from URL')
assertNotIncludes(productDetailPage, '/pages/merchant-detail/merchant-detail', 'product detail must not expose merchant route')

const cartPage = readText('pages/cart/cart.vue')
assertIncludes(cartPage, 'groups', 'cart page')
assertIncludes(cartPage, '配送包裹', 'cart split fulfillment copy')
assertIncludes(cartPage, '统一结算', 'cart combined checkout')
assertIncludes(cartPage, "uni.navigateTo({ url: '/pages/order-confirm/order-confirm' })", 'cart combined checkout navigation')
assertIncludes(cartPage, 'order-confirm/order-confirm?cart_id=', 'cart single package checkout uses cart id')
assertIncludes(cartPage, 'owner_id', 'cart owner grouping remains internal')
assertNotIncludes(cartPage, 'order-confirm/order-confirm?owner_id=', 'cart must not expose owner id in checkout URL')
assertNotIncludes(cartPage, '单独结算', 'cart must not expose package-level checkout')
assertNotIncludes(cartPage, 'group-checkout', 'cart must not expose package-level checkout button')
assertNotIncludes(cartPage, '请按配送包裹分别结算', 'cart must not block combined checkout')
assertNotIncludes(cartPage, '{{ item.code }}', 'cart must not foreground inventory product code')
assertNotIncludes(cartPage, '基本数量', 'cart buyer-facing quantity copy')
assertNotIncludes(cartPage, '折合', 'cart must not foreground base-unit conversion')
assertNotIncludes(cartPage, 'baseQty', 'cart must not compute buyer-facing base quantity')

const orderListPage = readText('pages/order-list/order-list.vue')
assertIncludes(orderListPage, "value: 'WAIT_PAY', name: '待付款'", 'order list buyer status tabs')
assertIncludes(orderListPage, "value: 'WAIT_SHIP', name: '待发货'", 'order list buyer status tabs')
assertIncludes(orderListPage, 'sale_mini_pending_order_status', 'order list shortcut status')
assertIncludes(orderListPage, 'order.is_combined', 'order list combined package summary')
assertIncludes(orderListPage, 'order.order_count', 'order list combined package summary')
assertNotIncludes(orderListPage, 'merchantOptions', 'order list must not expose merchant filter')
assertNotIncludes(orderListPage, 'switchMerchant', 'order list must not expose merchant filter')
assertNotIncludes(orderListPage, 'const ownerId', 'order list must not keep owner filter state')
assertNotIncludes(orderListPage, 'owner_id: ownerId.value', 'order list must not owner-filter orders')
assertNotIncludes(orderListPage, 'owner_name', 'order list must not show owner name')

const orderDetailPage = readText('pages/order-detail/order-detail.vue')
assertIncludes(orderDetailPage, '配送进度', 'order detail fulfillment progress')
assertIncludes(orderDetailPage, 'fulfillmentSteps', 'order detail fulfillment progress')
assertIncludes(orderDetailPage, 'order.is_combined', 'order detail combined package summary')
assertIncludes(orderDetailPage, '!order.value.is_combined && order.value.payment_status', 'order detail must not pay/refund combined order as a single package')
assertIncludes(orderDetailPage, '备货中', 'order detail unified fulfillment copy')
assertIncludes(orderDetailPage, '平台处理中', 'order detail unified fulfillment copy')
assertIncludes(orderDetailPage, '再来一单', 'order detail reorder entry')
assertIncludes(orderDetailPage, 'useCartStore', 'order detail reorder cart')
assertIncludes(orderDetailPage, 'config_id: line.config_id', 'order detail reorder config context')
assertIncludes(orderDetailPage, 'owner_id: order.value.owner_id || line.owner_id', 'order detail reorder owner context')
assertNotIncludes(orderDetailPage, 'owner_name', 'order detail must not show owner name')
assertNotIncludes(orderDetailPage, '· 基本', 'order detail buyer-facing quantity copy')
assertNotIncludes(orderDetailPage, 'product_code', 'order detail must not foreground inventory product code')
assertNotIncludes(orderDetailPage, 'base_qty', 'order detail must not foreground base-unit quantity')
assertNotIncludes(orderDetailPage, 'base_uom', 'order detail must not foreground base-unit quantity')
assertNotIncludes(orderDetailPage, '折合', 'order detail must not foreground base-unit conversion')

const statusTag = readText('components/OrderStatusTag.vue')
assertIncludes(statusTag, "props.status === 'WAIT_PAY'", 'order status tag buyer states')
assertIncludes(statusTag, "'REFUNDING'", 'order status tag buyer states')

const confirmPage = readText('pages/order-confirm/order-confirm.vue')
assertIncludes(confirmPage, 'const isCombined', 'order confirm combined checkout state')
assertIncludes(confirmPage, 'effectivePaymentMethod', 'order confirm combined checkout payment mode')
assertIncludes(confirmPage, "isCombined.value ? 'OFFLINE' : paymentMethod.value", 'combined checkout must not create unavailable wechat pay state')
assertIncludes(confirmPage, '多包裹订单会统一提交', 'order confirm combined checkout payment notice')
assertIncludes(confirmPage, '多包裹订单金额由服务端逐包裹校验后合计', 'order confirm combined checkout benefit notice')
assertIncludes(confirmPage, 'owner_id: ownerId.value || undefined', 'order confirm optional owner scope')
assertIncludes(confirmPage, 'address_id: address && !isCombined.value ? address.id : null', 'order confirm combined address fanout')
assertIncludes(confirmPage, 'cartId.value ? { cart_id: cartId.value } : ownerId.value ? { owner_id: ownerId.value } : {}', 'order confirm cart-id scoped load')
assertIncludes(confirmPage, 'order.orders && order.orders.length > 1', 'order confirm combined order response')
assertIncludes(confirmPage, "result=batch_offline", 'order confirm combined order result state')
assertIncludes(confirmPage, 'benefitService.coupons', 'order confirm coupons')
assertIncludes(confirmPage, 'benefitService.points', 'order confirm points')
assertIncludes(confirmPage, '/pages/order-result/order-result?id=${order.id}&result=batch_offline', 'order confirm combined result navigation')
assertIncludes(confirmPage, 'isPickup', 'order confirm pickup fulfillment')
assertIncludes(confirmPage, 'pickupTitle', 'order confirm pickup fulfillment')
assertIncludes(confirmPage, 'onDeliveryChange', 'order confirm delivery refresh')
assertIncludes(confirmPage, '自提联系人', 'order confirm pickup contact')
assertIncludes(confirmPage, '客户自提', 'order confirm pickup option')
assertIncludes(confirmPage, '下单后平台会尽快备货', 'order confirm unified fulfillment copy')
assertNotIncludes(confirmPage, 'owner_name', 'order confirm must not show owner name')
assertNotIncludes(confirmPage, 'query.owner_id', 'order confirm must not read owner id from URL')
assertNotIncludes(confirmPage, 'address/address?select=1${suffix}', 'order confirm must not expose owner id in address URL')
assertNotIncludes(confirmPage, 'batch_wait_pay', 'combined checkout must not expose unavailable aggregate wechat pay')

const resultPage = readText('pages/order-result/order-result.vue')
assertIncludes(resultPage, '订单摘要', 'order result summary')
assertIncludes(resultPage, '待付款提醒', 'order result pending payment')
assertIncludes(resultPage, '继续支付', 'order result payment retry')
assertIncludes(resultPage, '继续逛逛', 'order result shopping navigation')
assertIncludes(resultPage, 'const isBatch', 'order result combined package state')
assertIncludes(resultPage, 'paymentService.prepay', 'order result payment retry')
assertIncludes(resultPage, 'orderService.detail', 'order result loading')
assertIncludes(resultPage, "uni.switchTab({ url: '/pages/index/index' })", 'order result home navigation')
assertIncludes(resultPage, "uni.redirectTo({ url: `/pages/order-detail/order-detail?id=${id.value}` })", 'order result detail navigation')
assertNotIncludes(resultPage, 'owner_name', 'order result must not show owner name')
assertNotIncludes(resultPage, 'batch_wait_pay', 'order result must not expose unavailable aggregate wechat pay')

const benefitPage = readText('pages/benefits/benefits.vue')
assertIncludes(benefitPage, '博悦商城', 'benefit unified retail copy')
assertIncludes(benefitPage, 'benefitService.coupons()', 'benefit page aggregate coupons')
assertIncludes(benefitPage, 'benefitService.points()', 'benefit page aggregate points')
assertNotIncludes(benefitPage, 'query.owner_id', 'benefit page must not accept owner context from URL')
assertNotIncludes(benefitPage, 'requestedOwnerId', 'benefit page must not expose owner context selection')
assertNotIncludes(benefitPage, '.map((item) => item.owner)', 'benefit page must not expose owner context selection')
assertNotIncludes(benefitPage, 'picker', 'benefit page must not expose owner switch')

const afterSalesPage = readText('pages/after-sales/after-sales.vue')
assertIncludes(afterSalesPage, 'orderService.afterSales', 'after-sales page list')
assertIncludes(afterSalesPage, '从订单申请', 'after-sales page order flow')
assertNotIncludes(afterSalesPage, 'merchantOptions', 'after-sales must not expose merchant filter')
assertNotIncludes(afterSalesPage, 'switchMerchant', 'after-sales must not expose merchant filter')
assertNotIncludes(afterSalesPage, 'const ownerId', 'after-sales must not keep owner filter state')
assertNotIncludes(afterSalesPage, 'owner_id: ownerId.value', 'after-sales must not owner-filter requests')

const addressPage = readText('pages/address/address.vue')
assertIncludes(addressPage, 'const params = ownerId.value ? { owner_id: ownerId.value } : {}', 'address page filters only checkout owner context')
assertIncludes(addressPage, 'addressService.list(params)', 'address page aggregate list by default')
assertIncludes(addressPage, 'item.owner_id || ownerId.value', 'address page keeps internal edit context')
assertIncludes(addressPage, 'sale_mini_address_owner_id', 'address page receives internal checkout owner context through storage')
assertIncludes(addressPage, 'sale_mini_address_edit_owner_id', 'address page passes edit owner context through storage')
assertNotIncludes(addressPage, '.map((binding) => binding.owner)', 'address page must not expose owner context selection')
assertNotIncludes(addressPage, 'merchantOptions', 'address page must not expose owner switch')
assertNotIncludes(addressPage, 'selectMerchant', 'address page must not expose owner switch')
assertNotIncludes(addressPage, 'query.owner_id', 'address page must not read owner id from URL')
assertNotIncludes(addressPage, 'owner_id=${addressOwnerId}', 'address page must not expose owner id in edit URL')
assertNotIncludes(addressPage, 'owner_id=${ownerId.value}', 'address page must not expose owner id in create URL')

const addressEditPage = readText('pages/address-edit/address-edit.vue')
assertIncludes(addressEditPage, 'sale_mini_address_edit_owner_id', 'address edit keeps internal owner context through storage')
assertNotIncludes(addressEditPage, 'query.owner_id', 'address edit must not read owner id from URL')

const favoritesPage = readText('pages/favorites/favorites.vue')
assertIncludes(favoritesPage, '我的收藏', 'favorites page copy')
assertIncludes(favoritesPage, 'useBrowseStore', 'favorites page browse store')
assertIncludes(favoritesPage, 'cart.addProduct', 'favorites page cart revalidation')
assertIncludes(favoritesPage, 'config_id=${item.config_id', 'favorites page product detail context')

const historyPage = readText('pages/history/history.vue')
assertIncludes(historyPage, '浏览足迹', 'history page copy')
assertIncludes(historyPage, 'useBrowseStore', 'history page browse store')
assertIncludes(historyPage, 'cart.addProduct', 'history page cart revalidation')
assertIncludes(historyPage, 'config_id=${item.config_id', 'history page product detail context')

assertIncludes(userPage, 'getToken', 'user page auth guard')
assertIncludes(userPage, "uni.navigateTo({ url: '/pages/login/login' })", 'user page login redirect')

const tabDestinations = expectedTabPages.map((page) => `/${page}`)
const filesToScan = [
  'App.vue',
  'pages/index/index.vue',
  'pages/category/category.vue',
  'pages/product-list/product-list.vue',
  'pages/order-confirm/order-confirm.vue',
  'pages/order-result/order-result.vue',
  'pages/login/login.vue',
  'pages/user/user.vue',
  'utils/request.js',
  'stores/session.js',
]
for (const file of filesToScan) {
  const text = readText(file)
  for (const destination of tabDestinations) {
    assertNotIncludes(text, `navigateTo({ url: '${destination}'`, `${file} tab navigation`)
    assertNotIncludes(text, `redirectTo({ url: '${destination}'`, `${file} tab navigation`)
    assertNotIncludes(text, `reLaunch({ url: '${destination}'`, `${file} tab navigation`)
  }
}

console.log('sales-miniapp mall structure ok')

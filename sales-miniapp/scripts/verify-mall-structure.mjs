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
const expectedMallPages = [
  'pages/merchants/merchants',
  'pages/merchant-detail/merchant-detail',
  'pages/benefits/benefits',
  'pages/after-sales/after-sales',
  'pages/favorites/favorites',
  'pages/history/history',
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
for (const page of staleWorkbenchPages) {
  assert(!registeredPages.includes(page), `old sales workbench page is still registered: ${page}`)
  assertFileMissing(`${page}.vue`, 'old sales workbench page file')
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
]
for (const page of pagesJson.pages) {
  const pageFile = `${page.path}.vue`
  const text = readText(pageFile)
  for (const phrase of buyerPageForbiddenCopy) {
    assertNotIncludes(text, phrase, `${pageFile} buyer-facing copy`)
  }
}

const tabBar = pagesJson.tabBar || {}
const tabItems = tabBar.list || []
assert(tabItems.length === expectedTabPages.length, 'tabBar must have five mall tabs')
for (const [index, page] of expectedTabPages.entries()) {
  assert(tabItems[index].pagePath === page, `tab ${index} page must be ${page}`)
  assert(tabItems[index].text === expectedTabTexts[index], `tab ${index} text must be ${expectedTabTexts[index]}`)
}

assert(manifest.vueVersion === '3', 'sales miniapp must stay on Vue 3 uni-app')
assert(!Object.prototype.hasOwnProperty.call(manifest, 'uniCloud'), 'sales miniapp must not configure uniCloud')
assert(manifest.name === '博悦商城', 'manifest app name must be buyer-facing')
assert(manifest.h5 && manifest.h5.title === '博悦商城', 'h5 title must be buyer-facing')
assert(pagesJson.globalStyle.navigationBarTitleText === '博悦商城', 'global title must be buyer-facing')
assert(pagesJson.pages[0].style.navigationBarTitleText === '博悦商城', 'home title must be buyer-facing')

const request = readText('utils/request.js')
assertIncludes(request, '/api/sale-mini/home/', 'request API')
assertIncludes(request, '/api/sale-mini/merchants/', 'request API')
assertIncludes(request, '/api/sale-mini/categories/', 'request API')
assertIncludes(request, '/api/sale-mini/brands/', 'request API')
assertIncludes(request, '/api/sale-mini/coupons/', 'request API')
assertIncludes(request, '/api/sale-mini/points/', 'request API')
assertIncludes(request, '/api/sale-mini/after-sales/', 'request API')
assertIncludes(request, '/api/sale-mini/products/', 'request API')
assertIncludes(request, 'authRedirect: false', 'profile request')
assertIncludes(request, 'function mallMessage', 'buyer-facing error copy')
assertIncludes(request, '该商家暂未对你的账号开通购买权限', 'buyer-facing error copy')
assertNotIncludes(request, '/api/sales/mobile/', 'request API')
assertNotIncludes(request, 'createOrder', 'request API')
assertNotIncludes(request, 'quoteOrder', 'request API')
assertNotIncludes(request, 'submitOrder', 'request API')

const productService = readText('services/product.js')
assertIncludes(productService, 'api.saleMiniHome', 'product service')
assertIncludes(productService, 'api.saleMiniMerchants', 'product service')
assertIncludes(productService, 'api.saleMiniCategories', 'product service')
assertIncludes(productService, 'api.saleMiniBrands', 'product service')
assertIncludes(productService, 'api.saleMiniProducts', 'product service')
assertNotIncludes(productService, 'sales/mobile', 'product service')

const cartStore = readText('stores/cart.js')
assertIncludes(cartStore, 'config_id: product.config_id', 'cart add product config context')
assertIncludes(cartStore, 'owner_id: product.owner_id', 'cart add product owner context')

const browseStore = readText('stores/browse.js')
assertIncludes(browseStore, 'sale_mini_favorite_products', 'browse store favorites')
assertIncludes(browseStore, 'sale_mini_recent_products', 'browse store recent history')
assertIncludes(browseStore, 'config_id: product.config_id', 'browse store config context')
assertIncludes(browseStore, 'owner_id: product.owner_id', 'browse store owner context')

const indexPage = readText('pages/index/index.vue')
assertIncludes(indexPage, 'productService.home()', 'home page')
assertIncludes(indexPage, '@click="openBanner(banner)"', 'home banner navigation')
assertIncludes(indexPage, 'function openBanner', 'home banner navigation')
assertIncludes(indexPage, 'banner.link_type', 'home banner navigation')
assertIncludes(indexPage, 'banner.link_value', 'home banner navigation')
assertIncludes(indexPage, 'data.merchants', 'home merchant section')
assertIncludes(indexPage, 'productService.merchants()', 'home merchant section')
assertIncludes(indexPage, '精选商家', 'home merchant section')
assertIncludes(indexPage, '全部商家', 'home merchant entry')
assertIncludes(indexPage, '/pages/merchants/merchants', 'home merchant navigation')
assertIncludes(indexPage, '/pages/merchant-detail/merchant-detail?owner_id=${merchant.id}', 'home merchant store navigation')
assertIncludes(indexPage, '有货商品', 'home stock entry')
assertIncludes(indexPage, 'only_stock=1', 'home stock navigation')
assertIncludes(indexPage, '配送到', 'home fulfillment entry')
assertIncludes(indexPage, '门店自提', 'home fulfillment entry')
assertIncludes(indexPage, '领券中心', 'home benefit entry')
assertIncludes(indexPage, '会员积分', 'home benefit entry')
assertIncludes(indexPage, '售后服务', 'home after-sale entry')
assertIncludes(indexPage, '/pages/benefits/benefits', 'home benefit navigation')
assertIncludes(indexPage, '/pages/after-sales/after-sales', 'home after-sale navigation')
assertIncludes(indexPage, 'owner_id=${merchant.id}', 'home merchant navigation')
assertIncludes(indexPage, 'config_id=${product.config_id}', 'home product detail context')
assertIncludes(indexPage, 'productService', 'home page')
assertIncludes(indexPage, "uni.switchTab({ url: '/pages/category/category' })", 'home category navigation')
assertIncludes(indexPage, "uni.switchTab({ url: '/pages/cart/cart' })", 'home cart navigation')
assertIncludes(indexPage, "uni.switchTab({ url: '/pages/user/user' })", 'home user navigation')
assertNotIncludes(indexPage, '仓库商城', 'home buyer-facing copy')
assertNotIncludes(indexPage, '仓库严选', 'home buyer-facing copy')
assertNotIncludes(indexPage, '推荐采购', 'home buyer-facing copy')

const loginPage = readText('pages/login/login.vue')
assertIncludes(loginPage, '博悦商城', 'login buyer-facing copy')
assertIncludes(loginPage, '多商家商品选购', 'login buyer-facing copy')
assertNotIncludes(loginPage, '仓库商城', 'login buyer-facing copy')
assertNotIncludes(loginPage, '客户采购小程序', 'login buyer-facing copy')

const userPage = readText('pages/user/user.vue')
assertIncludes(userPage, '账号资料待完善', 'user buyer-facing copy')
assertIncludes(userPage, '优惠券与积分', 'user benefit entry')
assertIncludes(userPage, 'merchantOptions', 'user benefit merchant switch')
assertIncludes(userPage, 'changeMerchant', 'user benefit merchant switch')
assertIncludes(userPage, 'goBenefits', 'user benefit merchant switch')
assertIncludes(userPage, 'owner_id=${ownerId.value}', 'user benefit merchant switch')
assertIncludes(userPage, '我的收藏', 'user favorites entry')
assertIncludes(userPage, '/pages/favorites/favorites', 'user favorites navigation')
assertIncludes(userPage, '浏览足迹', 'user history entry')
assertIncludes(userPage, '/pages/history/history', 'user history navigation')
assertIncludes(userPage, '全部商家', 'user merchant entry')
assertIncludes(userPage, '/pages/merchants/merchants', 'user merchant navigation')
assertIncludes(userPage, '售后服务', 'user after-sale entry')
assertIncludes(userPage, '待付款', 'user order shortcuts')
assertIncludes(userPage, '待发货', 'user order shortcuts')
assertIncludes(userPage, 'sale_mini_pending_order_status', 'user order shortcuts')
assertNotIncludes(userPage, '未绑定仓库', 'user buyer-facing copy')
assertNotIncludes(userPage, '未绑定客户', 'user buyer-facing copy')

const sessionStore = readText('stores/session.js')
assertIncludes(sessionStore, 'bindings: data.bindings || []', 'wechat login multi-owner bindings')

const categoryPage = readText('pages/category/category.vue')
assertIncludes(categoryPage, "const ALL_CATEGORY_ID = 'all'", 'category page')
assertIncludes(categoryPage, "name: '全部'", 'category page')
assertIncludes(categoryPage, 'productService.merchants()', 'category merchant filter')
assertIncludes(categoryPage, 'productService.categories({ owner_id: ownerId.value })', 'category owner filter')
assertIncludes(categoryPage, 'productService.list', 'category page')
assertIncludes(categoryPage, 'owner_id: ownerId.value', 'category product owner filter')
assertIncludes(categoryPage, '全部商家', 'category merchant filter')
assertIncludes(categoryPage, 'config_id=${product.config_id}', 'category product detail context')

const productListPage = readText('pages/product-list/product-list.vue')
assertIncludes(productListPage, 'productService.merchants()', 'product list merchant filter')
assertIncludes(productListPage, 'productService.brands', 'product list brand filter')
assertIncludes(productListPage, 'owner_id: ownerId.value', 'product list owner filter')
assertIncludes(productListPage, 'brand_id: brandId.value', 'product list brand filter')
assertIncludes(productListPage, '全部商家', 'product list merchant filter')
assertIncludes(productListPage, '全部品牌', 'product list brand filter')
assertIncludes(productListPage, 'selectBrand', 'product list brand filter')
assertIncludes(productListPage, 'config_id=${product.config_id}', 'product list detail context')
assertIncludes(productListPage, 'price_desc', 'product list price sort')
assertIncludes(productListPage, "query.only_stock === '1'", 'product list stock query')
assertIncludes(productListPage, '搜索历史', 'product list search history')
assertIncludes(productListPage, '热门搜索', 'product list hot keywords')
assertIncludes(productListPage, 'sale_mini_search_history', 'product list search history storage')
assertIncludes(productListPage, 'clearSearch', 'product list clear search')
assertIncludes(productListPage, 'applyKeyword', 'product list keyword shortcut')

const productDetailPage = readText('pages/product-detail/product-detail.vue')
assertIncludes(productDetailPage, '同店热卖', 'product detail same merchant recommendations')
assertIncludes(productDetailPage, 'favoriteText', 'product detail favorite state')
assertIncludes(productDetailPage, 'toggleFavorite', 'product detail favorite action')
assertIncludes(productDetailPage, 'browse.addRecent(product.value)', 'product detail recent history')
assertIncludes(productDetailPage, 'purchaseNotice', 'product detail purchase permission notice')
assertIncludes(productDetailPage, 'canPurchaseCurrentOwner', 'product detail purchase permission notice')
assertIncludes(productDetailPage, 'session.fetchProfile()', 'product detail purchase permission notice')
assertIncludes(productDetailPage, '查看该商家全部商品', 'product detail merchant entry')
assertIncludes(productDetailPage, 'goMerchant', 'product detail merchant navigation')
assertIncludes(productDetailPage, '/pages/merchant-detail/merchant-detail?owner_id=${product.value.owner_id}', 'product detail merchant store navigation')
assertIncludes(productDetailPage, 'productService.detail(id, params)', 'product detail query context')
assertIncludes(productDetailPage, 'query.owner_id', 'product detail query owner context')
assertIncludes(productDetailPage, 'query.config_id', 'product detail query config context')
assertIncludes(productDetailPage, 'productDetailParams', 'product detail related product context')
assertIncludes(productDetailPage, 'loadSameMerchantProducts', 'product detail same merchant products')
assertIncludes(productDetailPage, 'productService.list', 'product detail same merchant products')
assertIncludes(productDetailPage, 'ProductCard', 'product detail related product card')

const cartPage = readText('pages/cart/cart.vue')
assertIncludes(cartPage, 'groups', 'cart page')
assertIncludes(cartPage, '请按商家分别结算', 'cart split checkout')
assertIncludes(cartPage, 'owner_id', 'cart owner grouping')
assertNotIncludes(cartPage, '基本数量', 'cart buyer-facing quantity copy')

const orderListPage = readText('pages/order-list/order-list.vue')
assertIncludes(orderListPage, "value: 'WAIT_PAY', name: '待付款'", 'order list buyer status tabs')
assertIncludes(orderListPage, "value: 'WAIT_SHIP', name: '待发货'", 'order list buyer status tabs')
assertIncludes(orderListPage, 'sale_mini_pending_order_status', 'order list shortcut status')
assertIncludes(orderListPage, 'merchantOptions', 'order list merchant filter')
assertIncludes(orderListPage, 'switchMerchant', 'order list merchant filter')
assertIncludes(orderListPage, 'owner_id: ownerId.value', 'order list merchant filter')
assertIncludes(orderListPage, 'session.fetchProfile()', 'order list merchant filter')
assertNotIncludes(orderListPage, "name: '待审核'", 'order list buyer status tabs')
assertNotIncludes(orderListPage, "name: '待确认'", 'order list buyer status tabs')

const orderDetailPage = readText('pages/order-detail/order-detail.vue')
assertIncludes(orderDetailPage, '配送进度', 'order detail fulfillment progress')
assertIncludes(orderDetailPage, 'fulfillmentSteps', 'order detail fulfillment progress')
assertIncludes(orderDetailPage, '再来一单', 'order detail reorder entry')
assertIncludes(orderDetailPage, 'useCartStore', 'order detail reorder cart')
assertIncludes(orderDetailPage, 'config_id: line.config_id', 'order detail reorder config context')
assertIncludes(orderDetailPage, 'owner_id: order.value.owner_id || line.owner_id', 'order detail reorder owner context')
assertIncludes(orderDetailPage, "uni.switchTab({ url: '/pages/cart/cart' })", 'order detail reorder cart navigation')
assertNotIncludes(orderDetailPage, '· 基本', 'order detail buyer-facing quantity copy')

const statusTag = readText('components/OrderStatusTag.vue')
assertIncludes(statusTag, "props.status === 'WAIT_PAY'", 'order status tag buyer states')
assertIncludes(statusTag, "'REFUNDING'", 'order status tag buyer states')

const confirmPage = readText('pages/order-confirm/order-confirm.vue')
assertIncludes(confirmPage, 'owner_id: ownerId.value', 'order confirm owner scope')
assertIncludes(confirmPage, 'cart.load({ owner_id: ownerId.value })', 'order confirm scoped cart load')
assertIncludes(confirmPage, 'benefitService.coupons', 'order confirm coupons')
assertIncludes(confirmPage, 'benefitService.points', 'order confirm points')
assertIncludes(confirmPage, 'isPickup', 'order confirm pickup fulfillment')
assertIncludes(confirmPage, 'pickupTitle', 'order confirm pickup fulfillment')
assertIncludes(confirmPage, 'onDeliveryChange', 'order confirm delivery refresh')
assertIncludes(confirmPage, '自提联系人', 'order confirm pickup contact')
assertIncludes(confirmPage, '客户自提', 'order confirm pickup option')

const benefitPage = readText('pages/benefits/benefits.vue')
assertIncludes(benefitPage, 'benefitService.coupons', 'benefit page coupons')
assertIncludes(benefitPage, 'benefitService.points', 'benefit page points')
assertIncludes(benefitPage, '切换商家', 'benefit page merchant switch')
assertIncludes(benefitPage, 'requestedOwnerId', 'benefit page owner query')
assertIncludes(benefitPage, 'syncRequestedMerchant', 'benefit page owner query')

const merchantsPage = readText('pages/merchants/merchants.vue')
assertIncludes(merchantsPage, 'productService.merchants', 'merchants page list')
assertIncludes(merchantsPage, '全部商家', 'merchants page copy')
assertIncludes(merchantsPage, '/pages/merchant-detail/merchant-detail?owner_id=${merchant.id}', 'merchants page store navigation')

const merchantDetailPage = readText('pages/merchant-detail/merchant-detail.vue')
assertIncludes(merchantDetailPage, '商家店铺', 'merchant detail page copy')
assertIncludes(merchantDetailPage, 'productService.merchants()', 'merchant detail data')
assertIncludes(merchantDetailPage, 'productService.categories({ owner_id: ownerId.value })', 'merchant detail categories')
assertIncludes(merchantDetailPage, 'productService.list', 'merchant detail products')
assertIncludes(merchantDetailPage, '客户自提', 'merchant detail fulfillment')
assertIncludes(merchantDetailPage, 'CartBar', 'merchant detail checkout')
assertIncludes(merchantDetailPage, 'config_id=${product.config_id}', 'merchant detail product context')
assertIncludes(merchantDetailPage, '/pages/benefits/benefits?owner_id=${ownerId.value}', 'merchant detail benefits')
assertIncludes(merchantDetailPage, '/pages/product-list/product-list?owner_id=${ownerId.value}', 'merchant detail all products')

const afterSalesPage = readText('pages/after-sales/after-sales.vue')
assertIncludes(afterSalesPage, 'orderService.afterSales', 'after-sales page list')
assertIncludes(afterSalesPage, '从订单申请', 'after-sales page order flow')
assertIncludes(afterSalesPage, 'merchantOptions', 'after-sales merchant filter')
assertIncludes(afterSalesPage, 'switchMerchant', 'after-sales merchant filter')
assertIncludes(afterSalesPage, 'orderService.afterSales({ owner_id: ownerId.value })', 'after-sales merchant filter')
assertIncludes(afterSalesPage, 'session.fetchProfile()', 'after-sales merchant filter')

const addressPage = readText('pages/address/address.vue')
assertIncludes(addressPage, 'merchantOptions', 'address page merchant switch')
assertIncludes(addressPage, 'selectMerchant', 'address page merchant switch')
assertIncludes(addressPage, 'session.fetchProfile()', 'address page merchant switch')
assertIncludes(addressPage, "addressService.list({ owner_id: ownerId.value })", 'address page owner scope')
assertIncludes(addressPage, "!selectable.value && merchantOptions.value.length > 1", 'address page selection owner lock')

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
  'pages/merchant-detail/merchant-detail.vue',
  'pages/product-list/product-list.vue',
  'pages/order-confirm/order-confirm.vue',
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

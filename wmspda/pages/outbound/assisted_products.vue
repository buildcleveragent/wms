<template>
  <view :class="['product-page', { 'is-wide': isWideScreen }]">
    <view v-if="checkingPermission" class="state-card">正在读取代办出库权限…</view>

    <template v-else-if="authorized">
      <view :class="['assisted-workspace', { 'wide-workspace': isWideScreen }]">
        <view class="product-pane">
          <view class="header-card">
            <view class="context-line">
              <text class="context-label">货主</text>
              <text>{{ draft.owner?.name || '-' }}</text>
              <text class="context-label customer-label">客户</text>
              <text>{{ draft.customer?.name || '-' }}</text>
            </view>
            <view class="search-row">
              <input
                ref="searchInputRef"
                v-model="query"
                class="input search-input assisted-product-search"
                placeholder="商品名称、货主商品编码、仓库SKU编码或条码"
                confirm-type="search"
                :confirm-hold="true"
                :disabled="submissionLocked"
                :focus="searchInputFocused"
                @confirm="loadProducts"
              />
              <button class="search-button" :disabled="loading || submissionLocked" @click="loadProducts">搜索</button>
              <button class="search-button" :disabled="loading || submissionLocked" @click="scanProduct">扫码</button>
            </view>
            <view v-if="!isWideScreen" class="history-shortcuts">
              <button class="shortcut-button" @click="openAssistedHistory">历史出库单</button>
              <button class="shortcut-button" @click="openAssistedStats">出库统计</button>
            </view>
          </view>

          <view v-if="searchOpen" class="section-card search-results-section">
            <view class="section-heading">
              <view>
                <text class="section-title">搜索结果</text>
                <text v-if="!loading" class="section-count">
                  （显示 {{ visibleProducts.length }} 项，共匹配 {{ products.length }} 项）
                </text>
              </view>
              <view class="result-heading-actions">
                <label v-if="noStockProductCount" class="stock-filter">
                  <switch
                    class="stock-filter-switch"
                    type="checkbox"
                    color="#1677ff"
                    :checked="!onlyInStock"
                    :disabled="submissionLocked"
                    @change="changeNoStockVisibility"
                  />
                  <text>显示无库存商品</text>
                </label>
                <button class="close-button" :disabled="submissionLocked" @click="closeSearchResults">关闭</button>
              </view>
            </view>

            <view v-if="!loading && hiddenNoStockCount" class="hidden-stock-hint">
              <text>另有 {{ hiddenNoStockCount }} 个匹配商品当前无可用库存</text>
              <text class="show-no-stock-link" @click="showAllNoStockProducts">显示无库存商品</text>
            </view>

            <view v-if="loading" class="empty">正在搜索商品…</view>
            <template v-else>
              <view v-if="isWideScreen && visibleProducts.length" class="result-table-header">
                <text>商品</text>
                <text>可用库存</text>
                <text>出库包装</text>
                <text>包装数量</text>
                <text>基本单价</text>
                <text>基本数量</text>
                <text>操作</text>
              </view>

              <view
                v-for="(product, index) in visibleProducts"
                :key="product.id"
                :class="[
                  'result-row',
                  {
                    odd: index % 2 === 0,
                    selected: isProductSelected(product.id),
                    'no-stock': !hasAvailableStock(product),
                  },
                ]"
              >
                <view class="product-identity">
                  <text v-if="product.gtin" class="product-gtin">{{ product.gtin }} · </text>
                  <text class="product-name">{{ product.name }}</text>
                  <text class="identity-meta"> · {{ product.sku || product.code || '-' }}</text>
                  <text v-if="product.spec" class="identity-meta"> · {{ product.spec }}</text>
                </view>

                <view class="stock-cell">
                  <template v-if="hasAvailableStock(product)">
                    <text class="mobile-label">可用：</text>{{ formatQty(product.available_qty) }}
                    {{ product.base_unit_name || product.base_unit || '' }}
                  </template>
                  <text v-else class="no-stock-label">当前仓库无可用库存</text>
                </view>

                <view class="unit-cell">
                  <text class="mobile-label">包装：</text>
                  <picker
                    :range="productUnitLabels(product)"
                    :value="selectedUnitIndex(product)"
                    :disabled="submissionLocked || isProductSelected(product.id) || !hasAvailableStock(product)"
                    @change="onResultUnitChange(product, $event)"
                  >
                    <view class="picker-field compact-picker">
                      {{ selectedUnit(product).label }} × {{ formatQty(selectedUnit(product).multiplier) }}
                    </view>
                  </picker>
                </view>

                <view class="input-cell">
                  <text class="mobile-label">数量（{{ selectedUnit(product).label }}）：</text>
                  <input
                    class="value-input"
                    type="digit"
                    :value="qtyMap[product.id] ?? ''"
                    placeholder="请输入"
                    :disabled="submissionLocked || isProductSelected(product.id) || !hasAvailableStock(product)"
                    :focus="focusedResultId === Number(product.id)"
                    @input="setResultQty(product.id, $event)"
                  />
                </view>

                <view class="input-cell">
                  <text class="mobile-label">基本单价：</text>
                  <input
                    class="value-input"
                    type="digit"
                    :value="priceMap[product.id] ?? ''"
                    placeholder="可不填"
                    :disabled="submissionLocked || isProductSelected(product.id) || !hasAvailableStock(product)"
                    @input="setResultPrice(product.id, $event)"
                  />
                </view>

                <view class="base-qty-cell">
                  <text class="mobile-label">折合：</text>{{ convertedBaseQty(product) }}
                  {{ product.base_unit_name || product.base_unit || '' }}
                </view>

                <button
                  :class="['row-button', { 'locate-button': isProductSelected(product.id) }]"
                  :disabled="submissionLocked || (!isProductSelected(product.id) && !hasAvailableStock(product))"
                  @click="isProductSelected(product.id) ? focusSelectedProduct(product.id) : addProduct(product)"
                >
                  {{ isProductSelected(product.id) ? '定位已选' : (hasAvailableStock(product) ? '加入' : '无库存') }}
                </button>
              </view>

              <view v-if="!products.length" class="empty">没有匹配商品</view>
              <view v-else-if="!visibleProducts.length" class="empty stock-empty">
                <view>匹配商品当前均无可用库存</view>
                <button class="show-stock-button" @click="showAllNoStockProducts">显示无库存商品</button>
              </view>
            </template>
          </view>

          <view class="section-card selected-section">
            <view class="section-heading">
              <view>
                <text class="section-title">已选商品</text>
                <text class="section-count">（{{ draft.itemCount }} 种）</text>
              </view>
              <text v-if="draft.itemCount" class="selected-total">
                基本数量 {{ formatQty(draft.totalQty) }}
              </text>
            </view>

            <view v-if="isWideScreen && draft.items.length" class="selected-table-header">
              <text>商品</text>
              <text>可用库存</text>
              <text>出库包装</text>
              <text>最终包装数量</text>
              <text>基本单价</text>
              <text>基本数量</text>
              <text>操作</text>
            </view>

            <view
              v-for="(item, index) in draft.items"
              :id="`selected-item-${item.product_id}`"
              :key="item.product_id"
              :class="['selected-row', { odd: index % 2 === 0, highlighted: highlightedProductId === item.product_id }]"
            >
              <view class="product-identity">
                <text class="product-name">{{ item.name }}</text>
                <text class="identity-meta"> · {{ item.sku || '-' }}</text>
                <text v-if="item.spec" class="identity-meta"> · {{ item.spec }}</text>
              </view>

              <view class="stock-cell">
                <text class="mobile-label">可用：</text>{{ formatQty(item.available_qty) }}
                {{ item.base_unit_name }}
              </view>

              <view class="narrow-selected-summary">
                已选 {{ formatQty(item.package_qty) }} {{ item.unit_label }}，折合
                {{ formatQty(item.qty) }} {{ item.base_unit_name }}
              </view>

              <view class="unit-cell">
                <text class="mobile-label">包装：</text>
                <picker
                  :range="itemUnitLabels(item)"
                  :value="itemUnitIndex(item)"
                  :disabled="submissionLocked"
                  @change="changeSelectedUnit(index, $event)"
                >
                  <view class="picker-field compact-picker">
                    {{ item.unit_label }} × {{ formatQty(item.unit_multiplier) }}
                  </view>
                </picker>
              </view>

              <view class="input-cell">
                <text class="mobile-label">数量（{{ item.unit_label }}）：</text>
                <input
                  class="value-input"
                  type="digit"
                  :value="item.package_qty"
                  :disabled="submissionLocked"
                  @input="setSelectedPackageQty(index, $event)"
                  @blur="finalizeSelectedPackageQty(index)"
                />
              </view>

              <view class="input-cell">
                <text class="mobile-label">基本单价：</text>
                <input
                  class="value-input"
                  type="digit"
                  :value="item.price"
                  placeholder="可不填"
                  :disabled="submissionLocked"
                  @input="setSelectedPrice(index, $event)"
                  @blur="finalizeSelectedPrice(index)"
                />
              </view>

              <view class="base-qty-cell">
                <text class="mobile-label">折合：</text>{{ formatQty(item.qty) }} {{ item.base_unit_name }}
              </view>

              <button class="row-button delete-button" :disabled="submissionLocked" @click="draft.remove(index)">删除</button>
            </view>

            <view v-if="!draft.items.length" class="empty initial-empty">
              请搜索商品名称、仓库SKU编码、条码或扫码选品
            </view>
          </view>
        </view>

        <view v-if="isWideScreen" class="order-pane">
          <AssistedOrderPanel
            mode="embedded"
            :show-items="false"
            @authorization-denied="handleAuthorizationDenied"
          />
          <AssistedOutboundHistoryPanel
            ref="historyPanelRef"
            @authorization-denied="handleAuthorizationDenied"
          />
        </view>
      </view>

      <view v-if="!isWideScreen" class="footer">
        <view class="cart-summary">
          已选 {{ draft.itemCount }} 种，基本数量共 {{ formatQty(draft.totalQty) }}
        </view>
        <button class="cart-button" :disabled="!draft.itemCount || submissionLocked" @click="goCart">
          查看出库单
        </button>
      </view>
    </template>

    <view v-else class="state-card">
      <view>当前账号无代办出库权限，或尚未选择货主和客户。</view>
      <button class="back-button" @click="backToStart">返回重新选择</button>
    </view>
  </view>
</template>

<script setup>
import { computed, nextTick, reactive, ref } from 'vue'
import { onLoad, onShow, onUnload } from '@dcloudio/uni-app'
import AssistedOrderPanel from '@/components/outbound/AssistedOrderPanel.vue'
import AssistedOutboundHistoryPanel from '@/components/outbound/AssistedOutboundHistoryPanel.vue'
import { useAuth } from '@/store/auth'
import { useAssistedOutbound } from '@/store/assistedOutbound'
import { api } from '@/utils/request'
import { scanOne } from '@/utils/scan'

const auth = useAuth()
const draft = useAssistedOutbound()
const checkingPermission = ref(true)
const authorized = ref(false)
const query = ref('')
const searchInputRef = ref(null)
const products = ref([])
const searchOpen = ref(false)
const loading = ref(false)
const onlyInStock = ref(true)
const forceShowNoStockForSearch = ref(false)
const searchInputFocused = ref(false)
const focusedResultId = ref(0)
const highlightedProductId = ref(0)
const historyPanelRef = ref(null)
const qtyMap = reactive({})
const priceMap = reactive({})
const WIDE_BREAKPOINT_PX = 1024
const windowWidth = ref(readWindowWidth())
const isWideScreen = computed(() => windowWidth.value >= WIDE_BREAKPOINT_PX)
const submissionLocked = computed(() => draft.submissionLocked === true)
const sortedProducts = computed(() => products.value
  .map((product, index) => ({ product, index }))
  .sort((left, right) => {
    const priorityDifference = resultPriority(left.product) - resultPriority(right.product)
    return priorityDifference || left.index - right.index
  })
  .map((entry) => entry.product))
const showNoStockForCurrentSearch = computed(() => (
  !onlyInStock.value
  || forceShowNoStockForSearch.value
  || products.value.length === 1
))
const visibleProducts = computed(() => sortedProducts.value.filter((product) => (
  showNoStockForCurrentSearch.value
  || hasAvailableStock(product)
  || isProductSelected(product.id)
)))
const noStockProductCount = computed(() => products.value.filter(
  (product) => !hasAvailableStock(product),
).length)
const hiddenNoStockCount = computed(() => {
  if (showNoStockForCurrentSearch.value) return 0
  return sortedProducts.value.filter((product) => (
    !hasAvailableStock(product) && !isProductSelected(product.id)
  )).length
})
let productRequestSequence = 0
let highlightTimer = null
let uniResizeListening = false
let h5ResizeHandler = null

function readWindowWidth() {
  if (typeof uni.getWindowInfo === 'function') {
    try {
      const width = Number(uni.getWindowInfo()?.windowWidth || 0)
      if (Number.isFinite(width) && width > 0) return width
    } catch (error) {
      console.warn('getWindowInfo 不可用，将回退到系统信息', error)
    }
  }
  try {
    return Number(uni.getSystemInfoSync()?.windowWidth || 0)
  } catch (error) {
    console.warn('无法读取窗口宽度，将使用窄屏布局', error)
  }
  return 0
}

function updateWindowWidth(event = null) {
  const nextWidth = Number(event?.size?.windowWidth ?? event?.windowWidth ?? readWindowWidth())
  if (Number.isFinite(nextWidth) && nextWidth > 0) windowWidth.value = nextWidth
}

function startResizeListener() {
  updateWindowWidth()
  if (typeof uni.onWindowResize === 'function' && typeof uni.offWindowResize === 'function') {
    try {
      uni.onWindowResize(updateWindowWidth)
      uniResizeListening = true
      return
    } catch (error) {
      console.warn('uni 窗口监听不可用，将尝试 H5 监听', error)
    }
  }

  // #ifdef H5
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    h5ResizeHandler = () => updateWindowWidth()
    window.addEventListener('resize', h5ResizeHandler)
  }
  // #endif
}

function stopResizeListener() {
  if (uniResizeListening && typeof uni.offWindowResize === 'function') {
    try {
      uni.offWindowResize(updateWindowWidth)
    } catch (error) {
      console.warn('取消 uni 窗口监听失败', error)
    }
  }
  uniResizeListening = false

  // #ifdef H5
  if (h5ResizeHandler && typeof window !== 'undefined') {
    window.removeEventListener('resize', h5ResizeHandler)
  }
  // #endif
  h5ResizeHandler = null
  if (highlightTimer) clearTimeout(highlightTimer)
}

function normalizeList(result) {
  if (Array.isArray(result)) return result
  return Array.isArray(result?.results) ? result.results : []
}

function eventValue(event) {
  return event?.detail?.value ?? event?.target?.value ?? ''
}

function formatQty(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? String(Number(number.toFixed(3))) : '0'
}

function isProductSelected(productId) {
  return draft.findItemIndex(productId) >= 0
}

function hasAvailableStock(product) {
  const available = Number(product?.available_qty ?? product?.available ?? 0)
  return Number.isFinite(available) && available > 0
}

function resultPriority(product) {
  if (isProductSelected(product?.id)) return 0
  return hasAvailableStock(product) ? 1 : 2
}

function changeNoStockVisibility(event) {
  if (submissionLocked.value) return
  onlyInStock.value = !Boolean(event?.detail?.value)
}

function showAllNoStockProducts() {
  if (submissionLocked.value) return
  onlyInStock.value = false
}

function productUnitOptions(product) {
  if (Array.isArray(product?.unitOptions) && product.unitOptions.length) return product.unitOptions
  return [
    {
      key: 'BASE',
      kind: 'base',
      label: product?.base_unit_name || product?.base_unit || '基本单位',
      multiplier: 1,
      package_id: null,
      barcode: '',
      is_base: true,
    },
  ]
}

function productUnitLabels(product) {
  return productUnitOptions(product).map(
    (option) => `${option.label}（×${formatQty(option.multiplier)}）`,
  )
}

function selectedUnitIndex(product) {
  const options = productUnitOptions(product)
  const index = Number(product?.selectedUnitIndex ?? 0)
  return Number.isInteger(index) && index >= 0 && index < options.length ? index : 0
}

function selectedUnit(product) {
  return productUnitOptions(product)[selectedUnitIndex(product)]
}

function itemUnitOptions(item) {
  if (Array.isArray(item?.unit_options) && item.unit_options.length) return item.unit_options
  return [
    {
      key: item?.package_id ?? 'BASE',
      kind: item?.package_id == null ? 'base' : 'package',
      label: item?.unit_label || item?.base_unit_name || item?.base_unit || '基本单位',
      multiplier: Number(item?.unit_multiplier || 1),
      package_id: item?.package_id ?? null,
      barcode: '',
      is_base: item?.package_id == null,
    },
  ]
}

function itemUnitLabels(item) {
  return itemUnitOptions(item).map(
    (option) => `${option.label}（×${formatQty(option.multiplier)}）`,
  )
}

function itemUnitIndex(item) {
  const index = itemUnitOptions(item).findIndex(
    (option) => (option.package_id ?? null) === (item.package_id ?? null),
  )
  return index >= 0 ? index : 0
}

function onResultUnitChange(product, event) {
  if (
    submissionLocked.value
    || isProductSelected(product.id)
    || !hasAvailableStock(product)
  ) return
  const index = Number(eventValue(event))
  const options = productUnitOptions(product)
  product.selectedUnitIndex = Number.isInteger(index) && index >= 0 && index < options.length
    ? index
    : 0
}

function convertedBaseQty(product) {
  const packageQty = Number(qtyMap[product.id])
  const multiplier = Number(selectedUnit(product)?.multiplier || 1)
  if (!Number.isFinite(packageQty) || packageQty <= 0) return '0'
  return formatQty(packageQty * multiplier)
}

function setResultQty(productId, event) {
  if (submissionLocked.value) return
  qtyMap[productId] = eventValue(event)
}

function setResultPrice(productId, event) {
  if (submissionLocked.value) return
  priceMap[productId] = eventValue(event)
}

function initializeResultInputs(rows) {
  rows.forEach((product) => {
    if (!hasAvailableStock(product) || qtyMap[product.id] === undefined) {
      qtyMap[product.id] = ''
    }
    if (priceMap[product.id] === undefined) {
      priceMap[product.id] = product.default_price ?? product.price ?? ''
    }
  })
}

function handleApiError(error) {
  if (Number(error?.statusCode || error?.code) === 403) {
    auth.invalidateAssistedCapability()
    authorized.value = false
  }
  console.error(error)
}

function closeSearchResults(options = {}) {
  productRequestSequence += 1
  products.value = []
  searchOpen.value = false
  loading.value = false
  forceShowNoStockForSearch.value = false
  focusedResultId.value = 0
  if (options.clearQuery !== false) query.value = ''
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

function focusSearchInput(delay = 80) {
  searchInputFocused.value = false
  nextTick(() => {
    setTimeout(() => {
      searchInputFocused.value = true
    }, delay)
  })
}

function readRenderedSearchInput() {
  let found = false
  let value = ''

  // #ifdef H5
  if (typeof document !== 'undefined') {
    const componentRoot = searchInputRef.value?.$el || searchInputRef.value
    const input = componentRoot?.matches?.('input')
      ? componentRoot
      : componentRoot?.querySelector?.('input')
        || document.querySelector('.assisted-product-search input')
        || document.querySelector('input.assisted-product-search')
    if (input && 'value' in input) {
      found = true
      value = String(input.value || '')
    }
  }
  // #endif

  return { found, value }
}

async function waitForStableQuery() {
  let previous = String(query.value || '').trim()
  let stableCount = 0
  for (let waited = 0; waited < 500; waited += 50) {
    await sleep(50)
    const current = String(query.value || '').trim()
    if (current === previous) {
      stableCount += 1
      if (stableCount >= 2) return current
    } else {
      previous = current
      stableCount = 0
    }
  }
  return String(query.value || '').trim()
}

async function loadProducts(event = null, options = {}) {
  if (submissionLocked.value) return
  const ownerId = Number(draft.owner?.id)
  const explicitKeyword = String(options?.keyword || '').trim()
  if (explicitKeyword) {
    query.value = explicitKeyword
  } else {
    const confirmedValue = String(eventValue(event) || '').trim()
    if (confirmedValue) query.value = confirmedValue
    const renderedInput = readRenderedSearchInput()
    if (renderedInput.found) query.value = renderedInput.value
  }
  const keyword = explicitKeyword || await waitForStableQuery()
  if (!ownerId) return
  if (!keyword) {
    uni.showToast({ title: '请输入商品名称、货主商品编码、仓库SKU编码或条码', icon: 'none' })
    return
  }

  query.value = keyword
  const requestSequence = ++productRequestSequence
  forceShowNoStockForSearch.value = options?.source === 'scan'
  searchOpen.value = true
  loading.value = true
  products.value = []
  focusedResultId.value = 0
  try {
    const result = await api.assistedProducts(ownerId, keyword, 1)
    if (requestSequence !== productRequestSequence || Number(draft.owner?.id) !== ownerId) return
    products.value = normalizeList(result)
    initializeResultInputs(products.value)
    if (
      products.value.length === 1
      && hasAvailableStock(products.value[0])
      && !isProductSelected(products.value[0].id)
    ) {
      focusedResultId.value = Number(products.value[0].id)
    }
    if (
      options?.source === 'scan'
      && products.value.length > 0
      && products.value.every((product) => !hasAvailableStock(product))
    ) {
      uni.showToast({
        title: '商品存在，但当前仓库无可用库存',
        icon: 'none',
      })
    }
  } catch (error) {
    handleApiError(error)
  } finally {
    if (requestSequence === productRequestSequence) loading.value = false
  }
}

function addProduct(product) {
  if (submissionLocked.value) return
  if (isProductSelected(product.id)) {
    focusSelectedProduct(product.id)
    return
  }
  if (!hasAvailableStock(product)) {
    uni.showToast({ title: '当前仓库无可用库存', icon: 'none' })
    return
  }

  const packageQty = Number(qtyMap[product.id])
  if (!Number.isFinite(packageQty) || packageQty <= 0) {
    uni.showToast({ title: '包装数量必须大于 0', icon: 'none' })
    return
  }

  const unit = selectedUnit(product)
  const multiplier = Number(unit?.multiplier || 1)
  const baseQty = Number((packageQty * multiplier).toFixed(3))
  if (!Number.isFinite(baseQty) || baseQty <= 0) {
    uni.showToast({ title: '包装换算数量无效，请联系管理员', icon: 'none' })
    return
  }

  const available = Number(product.available_qty ?? product.available ?? 0)
  if (baseQty > available) {
    uni.showToast({
      title: `基本数量 ${formatQty(baseQty)} 超过可用库存 ${formatQty(available)}`,
      icon: 'none',
    })
    return
  }

  const rawPrice = priceMap[product.id]
  if (rawPrice !== '' && rawPrice !== null && rawPrice !== undefined) {
    const price = Number(rawPrice)
    if (!Number.isFinite(price) || price < 0) {
      uni.showToast({ title: '单价必须为空或不小于 0', icon: 'none' })
      return
    }
  }

  const result = draft.selectItem(product, rawPrice, unit, packageQty)
  if (!result?.ok) {
    const title = result?.reason === 'already_selected'
      ? '该商品已经在已选列表中'
      : '无法加入商品，请检查数量和库存'
    uni.showToast({ title, icon: 'none' })
    return
  }

  qtyMap[product.id] = ''
  closeSearchResults()
  uni.showToast({
    title: `已加入 ${formatQty(packageQty)} ${unit.label}`,
    icon: 'none',
  })
  focusSearchInput()
}

function focusSelectedProduct(productId) {
  const normalizedId = Number(productId)
  if (draft.findItemIndex(normalizedId) < 0) return
  highlightedProductId.value = normalizedId
  if (highlightTimer) clearTimeout(highlightTimer)
  highlightTimer = setTimeout(() => {
    if (highlightedProductId.value === normalizedId) highlightedProductId.value = 0
  }, 1800)

  nextTick(() => {
    // #ifdef H5
    if (typeof document !== 'undefined') {
      document.getElementById(`selected-item-${normalizedId}`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    }
    // #endif
    if (typeof uni.pageScrollTo === 'function') {
      uni.pageScrollTo({ selector: `#selected-item-${normalizedId}`, duration: 250 })
    }
  })
}

function setSelectedPackageQty(index, event) {
  if (submissionLocked.value) return
  draft.setPackageQty(index, eventValue(event))
}

function finalizeSelectedPackageQty(index) {
  if (submissionLocked.value) return
  const item = draft.items[index]
  if (!item) return
  const result = draft.finalizePackageQty(index)
  if (!result?.ok) {
    if (['invalid_quantity', 'insufficient_stock'].includes(result?.reason)) {
      const name = item.name
      draft.remove(index)
      uni.showToast({ title: `${name} 数量无效，已从出库单移除`, icon: 'none' })
      return
    }
    uni.showToast({ title: `${item.name} 的包装换算无效`, icon: 'none' })
    return
  }
  if (result.clamped) {
    uni.showToast({
      title: `已按库存调整为 ${formatQty(result.package_qty)} ${item.unit_label}`,
      icon: 'none',
    })
  }
}

function setSelectedPrice(index, event) {
  if (submissionLocked.value) return
  draft.setPrice(index, eventValue(event))
}

function finalizeSelectedPrice(index) {
  if (submissionLocked.value) return
  const item = draft.items[index]
  if (!item) return
  const result = draft.finalizePrice(index)
  if (!result?.ok) {
    uni.showToast({ title: `${item.name} 的单价已清空，单价不能为负数`, icon: 'none' })
  }
}

function changeSelectedUnit(index, event) {
  if (submissionLocked.value) return
  const item = draft.items[index]
  if (!item) return
  const optionIndex = Number(eventValue(event))
  const options = itemUnitOptions(item)
  const selected = options[optionIndex]
  if (!selected) return
  const result = draft.setItemUnit(index, selected)
  if (!result?.ok) {
    uni.showToast({ title: '当前库存或数量不允许切换到该包装', icon: 'none' })
    return
  }
  if (result.clamped) {
    uni.showToast({
      title: `已按库存调整为 ${formatQty(result.package_qty)} ${item.unit_label}`,
      icon: 'none',
    })
  }
}

async function scanProduct() {
  if (submissionLocked.value) return
  const code = await scanOne()
  if (!code) return
  const keyword = String(code).trim()
  query.value = keyword
  await loadProducts(null, { source: 'scan', keyword })
}

function goCart() {
  if (!draft.itemCount || isWideScreen.value || submissionLocked.value) return
  uni.navigateTo({ url: '/pages/outbound/assisted_cart' })
}

function openAssistedHistory() {
  uni.navigateTo({ url: '/pages/outbound/assisted_history' })
}

function openAssistedStats() {
  uni.navigateTo({ url: '/pages/outbound/assisted_stats' })
}

function backToStart() {
  uni.redirectTo({ url: '/pages/outbound/assisted' })
}

function handleAuthorizationDenied() {
  authorized.value = false
}

async function initialize() {
  checkingPermission.value = true
  try {
    await auth.ensureProfile()
    authorized.value = Boolean(
      auth.canProcessAssistedOutbound && draft.owner?.id && draft.customer?.id,
    )
  } catch (error) {
    console.warn('无法读取代办权限', error)
  } finally {
    checkingPermission.value = false
  }
}

onLoad(() => {
  startResizeListener()
  initialize()
})
onShow(() => {
  if (authorized.value && isWideScreen.value) historyPanelRef.value?.refresh?.()
})
onUnload(stopResizeListener)
</script>

<style scoped>
.product-page { box-sizing: border-box; min-height: 100vh; padding: 16rpx 16rpx 150rpx; background: #f6f7fb; }
.product-page.is-wide { padding-bottom: 24rpx; }
.state-card { margin: 18rpx 0; padding: 24rpx; border-radius: 16rpx; background: #fff; }
.header-card { position: sticky; top: 0; z-index: 20; box-sizing: border-box; margin-bottom: 16rpx; padding: 16rpx; border: 1rpx solid #e1e5eb; border-radius: 14rpx; background: #fff; box-shadow: 0 2rpx 12rpx rgba(0,0,0,.08); }
.context-line { display: flex; align-items: center; gap: 10rpx; overflow: hidden; font-size: 25rpx; white-space: nowrap; }
.context-label { color: #697386; }
.customer-label { margin-left: 18rpx; }
.search-row { display: flex; align-items: center; gap: 12rpx; margin-top: 12rpx; }
.search-input { min-width: 0; margin: 0; flex: 1 1 auto; }
.search-button, .back-button { flex: none; margin: 0; padding: 0 20rpx; height: 64rpx; line-height: 64rpx; color: #1677ff; background: #eef5ff; font-size: 24rpx; }
.history-shortcuts { display: flex; justify-content: flex-end; gap: 12rpx; margin-top: 12rpx; }
.shortcut-button { flex: none; margin: 0; padding: 0 18rpx; height: 56rpx; line-height: 56rpx; color: #475569; background: #f1f5f9; font-size: 22rpx; }
.assisted-workspace, .product-pane, .order-pane { min-width: 0; }
.wide-workspace { display: grid; grid-template-columns: minmax(0, 13fr) minmax(360px, 7fr); align-items: start; gap: 18rpx; }
.is-wide .product-pane, .order-pane { max-height: calc(100vh - 40rpx); overflow-y: auto; overscroll-behavior: contain; }
.order-pane { border-radius: 16rpx; }
.section-card { margin-bottom: 16rpx; overflow-x: auto; border-radius: 14rpx; background: #fff; box-shadow: 0 3rpx 14rpx rgba(15,23,42,.04); }
.section-heading { display: flex; align-items: center; justify-content: space-between; min-height: 64rpx; padding: 10rpx 16rpx; border-bottom: 1rpx solid #e5e7eb; }
.section-title { font-size: 28rpx; font-weight: 700; }
.section-count, .selected-total { color: #64748b; font-size: 22rpx; }
.result-heading-actions, .stock-filter { display: flex; align-items: center; gap: 10rpx; }
.result-heading-actions { flex: none; margin-left: 12rpx; }
.stock-filter { color: #475569; font-size: 21rpx; white-space: nowrap; }
.stock-filter-switch { transform: scale(.72); transform-origin: right center; }
.close-button { margin: 0; padding: 0 16rpx; height: 50rpx; line-height: 50rpx; color: #475569; background: #f1f5f9; font-size: 22rpx; }
.hidden-stock-hint { display: flex; align-items: center; justify-content: space-between; gap: 14rpx; padding: 12rpx 16rpx; color: #9a6700; background: #fff8e6; font-size: 22rpx; }
.show-no-stock-link { flex: none; color: #1677ff; font-weight: 600; }
.result-row, .selected-row { padding: 18rpx; border-bottom: 1rpx solid #e5e7eb; background: #fff; transition: background-color .2s, box-shadow .2s; }
.result-row.odd, .selected-row.odd { background: #fafbfc; }
.result-row.selected { background: #f0f7ff; }
.result-row.no-stock, .result-row.no-stock.odd { color: #8993a3; background: #f3f5f7; }
.result-row.no-stock.selected { background: #edf2f7; box-shadow: inset 3rpx 0 0 #8aa4c2; }
.result-row.no-stock .product-gtin, .result-row.no-stock .product-name, .result-row.no-stock .identity-meta { color: #788394; }
.result-row.no-stock .picker-field, .result-row.no-stock .value-input { color: #98a1af; background: #e9edf2; }
.no-stock-label { color: #d46b08; font-weight: 600; white-space: normal; }
.selected-row.highlighted { position: relative; z-index: 1; background: #fff7d6; box-shadow: inset 0 0 0 2rpx #f6ad24; }
.product-identity { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.product-gtin { color: #697386; font-size: 22rpx; }
.product-name { font-size: 27rpx; font-weight: 700; }
.identity-meta, .stock-cell { color: #697386; font-size: 22rpx; }
.stock-cell, .unit-cell, .input-cell, .base-qty-cell { margin-top: 10rpx; }
.picker-field, .value-input { box-sizing: border-box; width: 100%; height: 58rpx; padding: 0 12rpx; border: 1rpx solid #d9dee7; border-radius: 8rpx; background: #fff; }
.picker-field { overflow: hidden; line-height: 58rpx; text-overflow: ellipsis; white-space: nowrap; }
.base-qty-cell { color: #1677ff; font-size: 23rpx; }
.row-button { margin: 12rpx 0 0; padding: 0 14rpx; height: 56rpx; line-height: 56rpx; color: #fff; background: #1677ff; font-size: 22rpx; }
.locate-button { color: #1677ff; background: #eaf3ff; }
.delete-button { color: #c62828; background: #fff1f0; }
.row-button[disabled] { opacity: .45; }
.narrow-selected-summary { margin-top: 10rpx; color: #1677ff; font-size: 23rpx; }
.selected-row .unit-cell, .selected-row .input-cell, .selected-row .base-qty-cell { display: none; }
.empty { padding: 54rpx 20rpx; text-align: center; color: #8a94a6; }
.stock-empty { color: #9a6700; }
.show-stock-button { margin-top: 16rpx; padding: 0 20rpx; height: 56rpx; line-height: 56rpx; color: #1677ff; background: #eef5ff; font-size: 22rpx; }
.initial-empty { min-height: 100rpx; }
.footer { position: fixed; right: 0; bottom: 0; left: 0; z-index: 20; display: flex; align-items: center; justify-content: space-between; padding: 18rpx 24rpx; background: rgba(255,255,255,.98); box-shadow: 0 -4rpx 16rpx rgba(0,0,0,.08); }
.cart-summary { font-size: 27rpx; font-weight: 600; }
.cart-button { flex: none; margin: 0; color: #fff; background: #1677ff; }
.cart-button[disabled] { opacity: .45; }
.mobile-label { color: #64748b; }

@media (min-width: 1024px) {
  .result-table-header, .result-row, .selected-table-header, .selected-row {
    box-sizing: border-box;
    display: grid;
    grid-template-columns: minmax(150px, 1.8fr) minmax(85px, .8fr) minmax(110px, 1fr) minmax(90px, .85fr) minmax(85px, .8fr) minmax(85px, .8fr) auto;
    align-items: center;
    gap: 10rpx;
    min-width: 850px;
  }
  .result-table-header, .selected-table-header { padding: 10rpx 14rpx; color: #64748b; background: #eef2f7; font-size: 21rpx; font-weight: 600; text-align: center; }
  .result-table-header > text:first-child, .selected-table-header > text:first-child { text-align: left; }
  .result-row, .selected-row { min-height: 70rpx; padding: 7rpx 14rpx; }
  .selected-row .unit-cell, .selected-row .input-cell, .selected-row .base-qty-cell { display: block; }
  .narrow-selected-summary { display: none; }
  .product-identity, .stock-cell, .compact-picker, .base-qty-cell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .product-name { font-size: 24rpx; }
  .stock-cell, .unit-cell, .input-cell, .base-qty-cell, .row-button { margin: 0; }
  .value-input, .picker-field { height: 54rpx; }
  .picker-field { line-height: 54rpx; }
  .base-qty-cell { text-align: center; }
  .row-button { height: 54rpx; line-height: 54rpx; }
  .mobile-label { display: none; }
}

@media (max-width: 1023px) {
  .section-heading { align-items: flex-start; }
  .result-heading-actions { flex-wrap: wrap; justify-content: flex-end; }
  .hidden-stock-hint { align-items: flex-start; }
}
</style>

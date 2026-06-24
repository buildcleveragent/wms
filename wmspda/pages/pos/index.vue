<template>
  <view class="pos-page">
    <view class="topbar">
      <button class="nav-back" @click="goBack">‹</button>
      <text class="title">POS收银</text>
      <view class="nav-spacer"></view>
    </view>

    <view class="section shift-section">
      <view class="section-head">
        <text class="section-title">当前班次</text>
        <view class="section-actions">
          <button class="ghost-btn shift-refresh-btn" :disabled="shiftLoading" @click="loadCurrentShift">
            刷新
          </button>
          <button v-if="currentShift" class="ghost-btn shift-refresh-btn" @click="exportCurrentShift">
            导出
          </button>
        </view>
      </view>
      <view v-if="currentShift" class="shift-card">
        <view class="shift-main">
          <text class="shift-no">{{ currentShift.shift_no }}</text>
          <text class="shift-meta">{{ currentShift.cashier_username || '-' }} / {{ shiftStatusText(currentShift.status) }}</text>
          <text class="shift-meta">开班 {{ formatDateTime(currentShift.opened_at) }}</text>
        </view>
        <view class="shift-numbers">
          <text>净销售 {{ money(shiftSummary.net_amount) }}</text>
          <text>完成 {{ shiftSummary.completed_count || 0 }} 单 / 作废 {{ shiftSummary.voided_count || 0 }} 单</text>
          <text>现金应点 {{ money(shiftSummary.expected_cash_amount) }}</text>
        </view>
        <view v-if="currentShift.status === 'OPEN'" class="shift-close-row">
          <input class="input shift-cash-input" v-model.trim="shiftActualCashAmount" type="digit" placeholder="现金实点金额" />
          <button class="primary-btn shift-action-btn" :disabled="shiftLoading" @click="closeCurrentShift">
            交班
          </button>
        </view>
      </view>
      <view v-else class="shift-open-row">
        <input class="input shift-cash-input" v-model.trim="shiftOpeningCashAmount" type="digit" placeholder="备用金" />
        <button class="primary-btn shift-action-btn" :disabled="shiftLoading" @click="openCurrentShift">
          开班
        </button>
        <text class="shift-hint">开班后才能结账</text>
      </view>
    </view>

    <view class="pos-main">
      <view class="pos-left">
    <view class="section pos-toolbar">
      <view class="scan-line">
        <text class="toolbar-label primary-label">商品</text>
        <input
          class="input scan-input"
          :value="productKeyword"
          :focus="productInputFocus"
          placeholder="扫码或输入商品名称/编码/SKU/条码"
          confirm-type="search"
          :confirm-hold="true"
          @focus="productInputFocus = true"
          @blur="productInputFocus = false"
          @input="onProductInput"
          @confirm="handleProductConfirm"
        />
        <button class="ghost-btn scan-btn" @click="triggerQuickScan">扫码</button>
        <button class="primary-btn scan-btn" :disabled="productLoading" @click="searchProducts">搜索</button>
        <button class="ghost-btn clear-btn" @click="confirmResetSale">清空</button>
      </view>

      <view v-if="lastScan || scanFeedbackMessage" :class="['scan-feedback', scanFeedbackType]">
        <text v-if="lastScan" class="scan-code">最近扫码：{{ lastScan }}</text>
        <text v-if="scanFeedbackMessage" class="scan-message">{{ scanFeedbackMessage }}</text>
      </view>

      <view class="product-list" v-if="products.length">
        <view class="product-row" v-for="product in products" :key="product.id">
          <view class="product-main">
            <text class="product-name">{{ product.name || product.sku || product.code }}</text>
            <text class="product-meta">{{ product.code }} {{ product.sku || '' }}</text>
            <text class="product-meta">
              售价 {{ money(product.price) }} / 可售 {{ qtyText(stockAvailableQty(product), product) }}
            </text>
          </view>
          <button class="primary-btn add-btn" :disabled="!hasAvailableStock(product)" @click="addToCart(product)">
            {{ hasAvailableStock(product) ? '加入' : '无货' }}
          </button>
        </view>
      </view>
      <view class="empty-tip" v-else-if="productSearched && !productLoading && !scanFeedbackMessage">未找到商品</view>
    </view>
    <view class="section cart-section">
      <view class="section-head">
        <text class="section-title">购物车</text>
        <text class="selected-text">{{ cartItems.length }}项 / {{ money(totalAmount) }}</text>
      </view>

      <view v-if="!cartItems.length" class="empty-cart">扫码或搜索商品后加入购物车</view>

      <view v-else class="cart-table">
        <view
          class="cart-table-head cart-line-row"
          style="display: flex; flex-direction: row; align-items: center; flex-wrap: nowrap; width: 100%;"
        >
          <view class="cart-goods-col" style="flex: 1 1 auto; min-width: 0; padding-right: 12rpx; box-sizing: border-box;">商品</view>
          <view class="cart-unit-col" style="width: 96rpx; flex: 0 0 96rpx; margin-left: 8rpx; box-sizing: border-box;">单位</view>
          <view class="cart-qty-col" style="width: 130rpx; flex: 0 0 130rpx; margin-left: 8rpx; box-sizing: border-box;">数量</view>
          <view class="cart-price-col" style="width: 150rpx; flex: 0 0 150rpx; margin-left: 8rpx; box-sizing: border-box;">单价</view>
          <view class="cart-amount-col" style="width: 160rpx; flex: 0 0 160rpx; margin-left: 8rpx; box-sizing: border-box;">金额</view>
          <view class="cart-action-col" style="width: 70rpx; flex: 0 0 70rpx; margin-left: 8rpx; text-align: center; box-sizing: border-box;">操作</view>
        </view>

        <view
          v-for="(item, index) in cartItems"
          :key="item.product_id"
          class="cart-row cart-line-row"
          style="display: flex; flex-direction: row; align-items: center; flex-wrap: nowrap; width: 100%;"
        >
          <view class="cart-info cart-goods-col" style="flex: 1 1 auto; min-width: 0; padding-right: 12rpx; box-sizing: border-box;">
            <text class="cart-goods-line">
              {{ item.name }}　{{ item.code }} / 可售 {{ qtyText(item.available_qty, item) }} {{ item.base_unit_name }} / 基本数量 {{ qtyText(lineBaseQty(item), item) }} {{ item.base_unit_name }}
            </text>
          </view>

          <view class="cart-unit-col" style="width: 96rpx; flex: 0 0 96rpx; margin-left: 8rpx; box-sizing: border-box;">
            <picker
              class="unit-picker"
              mode="selector"
              :range="item.unit_labels"
              :value="item.unit_index"
              @change="changeUnit(index, $event)"
            >
              <view class="picker-value">{{ item.unit_labels[item.unit_index] }}</view>
            </picker>
          </view>

          <view class="cart-qty-col" style="width: 130rpx; flex: 0 0 130rpx; margin-left: 8rpx; box-sizing: border-box;">
            <input
              class="small-input"
              :type="isCountItem(item) ? 'number' : 'digit'"
              v-model="item.qty"
              @blur="normalizeLine(index)"
            />
          </view>

          <view class="cart-price-col" style="width: 150rpx; flex: 0 0 150rpx; margin-left: 8rpx; box-sizing: border-box;">
            <view class="price-wrap">
              <text class="yuan">¥</text>
              <input
                class="price-input"
                type="digit"
                v-model="item.price"
                @blur="normalizeLine(index)"
              />
            </view>
          </view>

          <view class="cart-amount-col" style="width: 160rpx; flex: 0 0 160rpx; margin-left: 8rpx; box-sizing: border-box;">
            <text class="line-amount">{{ money(lineAmount(item)) }}</text>
          </view>

          <view class="cart-action-col" style="width: 70rpx; flex: 0 0 70rpx; margin-left: 8rpx; text-align: center; box-sizing: border-box;">
            <button class="danger-btn remove-btn" @click="removeLine(index)">删</button>
          </view>
        </view>
      </view>
    </view>
      </view>
      <view class="pos-right">

    <view class="section customer-panel">
      <view class="section-head">
        <text class="section-title">客户信息</text>
        <text class="selected-text">{{ selectedCustomerName }}</text>
      </view>
      <view class="customer-search-row">
        <input
          class="input customer-input"
          v-model.trim="customerKeyword"
          placeholder="散客 / 客户名称 / 编码"
          confirm-type="search"
          @confirm="searchCustomers"
        />
        <button class="primary-btn customer-search-btn" :disabled="customerLoading" @click="searchCustomers">搜索</button>
      </view>
      <view class="customer-list" v-if="customers.length">
        <view
          v-for="customer in customers"
          :key="customer.id"
          :class="['choice-row', { active: selectedCustomer && selectedCustomer.id === customer.id }]"
          @click="selectCustomer(customer)"
        >
          <view>
            <text class="choice-name">{{ customer.name || customer.code || customer.id }}</text>
            <text class="choice-code">ID: {{ customer.id }} {{ customer.code || '' }}</text>
          </view>
          <text class="choice-check" v-if="selectedCustomer && selectedCustomer.id === customer.id">
            已选
          </text>
        </view>
      </view>
      <view class="doc-info-stack">
        <view class="doc-info-item">
          <text class="doc-label">日期</text>
          <text class="doc-value">{{ saleDateText }}</text>
        </view>
        <view class="doc-info-item">
          <text class="doc-label">小票号</text>
          <input class="input doc-input" v-model.trim="srcBillNo" placeholder="小票号" />
        </view>
      </view>
    </view>

    <view class="submit-panel">
      <view class="checkout-row">
        <view class="bill-row remark-row">
          <text class="bill-label">备注</text>
          <input class="bill-input" v-model.trim="remark" placeholder="可选" />
        </view>
        <view class="bill-row payment-row">
          <text class="bill-label">支付</text>
          <picker
            class="payment-picker"
            mode="selector"
            :range="paymentMethodLabels"
            :value="paymentMethodIndex"
            @change="changePaymentMethod"
          >
            <view class="payment-value">{{ selectedPaymentMethod.label }}</view>
          </picker>
          <input
            class="payment-input"
            type="digit"
            v-model="amountReceived"
            placeholder="实收"
            :disabled="paymentMethod !== 'CASH'"
          />
        </view>
        <view class="bill-row reference-row">
          <text class="bill-label">参考号</text>
          <input class="bill-input" v-model.trim="paymentReferenceNo" placeholder="支付流水号/可选" />
        </view>
        <view class="summary-row">
          <view class="amount-due">
            <text class="amount-label">应收金额</text>
            <text class="summary-title">{{ money(totalAmount) }}</text>
          </view>
          <view class="amount-grid">
            <view class="amount-box">
              <text class="amount-label">实收</text>
              <text class="amount-value">{{ money(receivedAmount) }}</text>
            </view>
            <view class="amount-box">
              <text class="amount-label">找零</text>
              <text class="amount-value change-value">{{ money(changeAmount) }}</text>
            </view>
          </view>
          <view class="summary-sub">
            <text>商品 {{ cartItems.length }} 项</text>
            <text>基本数量 {{ qtyText(totalBaseQty) }}</text>
            <text v-if="ownerCount > 1">货主 {{ ownerCount }} 个，将自动拆单</text>
          </view>
          <view v-if="selectedCustomer && ownerCount > 1" class="owner-warning">
            已选客户仅用于同货主订单，其他货主自动使用散客
          </view>
          <checkbox-group class="print-check-group" @change="onAutoPrintChange">
            <label class="print-option">
              <checkbox value="auto" :checked="autoPrintSale" color="#1677ff" />
              <text>自动打印销售单</text>
            </label>
          </checkbox-group>
          <button class="submit-btn" :disabled="!canCheckout || submitting" @click="checkout">
            结账
          </button>
        </view>
      </view>
    </view>

    <view class="section stats-section">
      <view class="section-head">
        <text class="section-title">今日统计</text>
        <view class="section-actions">
          <button class="ghost-btn stats-refresh-btn" :disabled="statsLoading" @click="loadPosStats({ force: true })">
            刷新
          </button>
          <button class="ghost-btn stats-refresh-btn" @click="exportTodayStats">
            导出
          </button>
        </view>
      </view>
      <view class="stats-grid">
        <view class="stats-card primary">
          <text class="stats-label">净销售</text>
          <text class="stats-value">{{ money(statsSummary.net_amount) }}</text>
        </view>
        <view class="stats-card">
          <text class="stats-label">完成单</text>
          <text class="stats-value">{{ statsSummary.completed_count || 0 }}</text>
        </view>
        <view class="stats-card">
          <text class="stats-label">作废单</text>
          <text class="stats-value danger">{{ statsSummary.voided_count || 0 }}</text>
        </view>
        <view class="stats-card">
          <text class="stats-label">作废金额</text>
          <text class="stats-value danger">{{ money(statsSummary.voided_amount) }}</text>
        </view>
      </view>
      <view class="stats-payments" v-if="statsPayments.length">
        <view class="stats-payment-row" v-for="row in statsPayments" :key="row.method || row.method_label">
          <text>{{ row.method_label || paymentMethodName(row.method) }}</text>
          <text>{{ money(row.amount) }} / {{ row.sale_count || 0 }} 单</text>
        </view>
      </view>
    </view>

    <view class="section receipt-section" v-if="lastReceipt">
      <view class="section-head">
        <text class="section-title">最近小票</text>
        <text class="selected-text">{{ lastReceipt.sale_no || lastReceipt.src_bill_no }}</text>
      </view>
      <button class="ghost-btn receipt-print-btn" :disabled="!lastSalePrintData" @click="printLastSale">
        打印销售单
      </button>
      <view class="receipt-row">
        <text>应收 {{ money(lastReceipt.total_amount) }}</text>
        <text>
          {{ paymentMethodName(lastReceipt.payment?.method) }}
          实收 {{ money(lastReceipt.payment?.amount_received) }}
        </text>
      </view>
      <view class="receipt-row">
        <text>找零 {{ money(lastReceipt.payment?.change_amount) }}</text>
        <text>出库单 {{ (lastReceipt.orders || []).length }} 张</text>
      </view>
      <view class="receipt-row" v-if="orderNosText(lastReceipt.orders)">
        <text>出库单号</text>
        <text class="receipt-nos">{{ orderNosText(lastReceipt.orders) }}</text>
      </view>
    </view>

    <view class="section history-section">
      <view class="section-head">
        <text class="section-title">销售历史</text>
        <button class="ghost-btn history-refresh-btn" :disabled="historyLoading" @click="loadPosSaleHistory({ force: true })">
          刷新
        </button>
      </view>
      <view class="history-search-row">
        <input
          class="input history-input"
          v-model.trim="historyKeyword"
          placeholder="小票号 / POS单号"
          confirm-type="search"
          @confirm="loadPosSaleHistory({ force: true })"
        />
        <button class="primary-btn history-search-btn" :disabled="historyLoading" @click="loadPosSaleHistory({ force: true })">
          查询
        </button>
      </view>

      <view v-if="pendingVoidSale" class="void-panel">
        <text class="void-title">作废 {{ saleDisplayNo(pendingVoidSale) }}</text>
        <input class="input void-input" v-model.trim="voidReason" placeholder="请输入作废原因" />
        <view class="void-actions">
          <button class="ghost-btn void-action-btn" @click="cancelVoidSale">取消</button>
          <button class="danger-btn void-action-btn" @click="confirmVoidSale">确认作废</button>
        </view>
      </view>

      <view v-if="!historySales.length && !historyLoading" class="empty-tip">暂无销售记录</view>
      <view v-else class="history-list">
        <view class="history-row" v-for="sale in historySales" :key="sale.id">
          <view class="history-main">
            <view class="history-title-row">
              <text class="history-no">{{ saleDisplayNo(sale) }}</text>
              <text :class="['history-status', isVoidedSale(sale) ? 'voided' : 'completed']">
                {{ saleStatusText(sale.status) }}
              </text>
            </view>
            <text class="history-meta">
              {{ saleCreatedText(sale) }} / {{ salePaymentMethod(sale) }} / {{ money(sale.total_amount) }}
            </text>
            <text class="history-meta">
              出库单 {{ saleOrderCount(sale) }} 张 {{ orderNosText(sale.orders) }}
            </text>
          </view>
          <view class="history-actions">
            <button class="ghost-btn history-action-btn" @click="reprintSale(sale)">重打</button>
            <button class="danger-btn history-action-btn" :disabled="isVoidedSale(sale)" @click="startVoidSale(sale)">作废</button>
          </view>
        </view>
      </view>
    </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { onHide, onShow } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'

const customerKeyword = ref('')
const customers = ref([])
const selectedCustomer = ref(null)
const customerLoading = ref(false)

const productKeyword = ref('')
const productInputFocus = ref(true)
const products = ref([])
const productLoading = ref(false)
const productSearched = ref(false)
const scanFeedbackMessage = ref('')
const scanFeedbackType = ref('info')
const productLookupQueue = []
let productLookupRunning = false
let lastProductLookup = { keyword: '', time: 0 }
let cartStockRefreshing = false
let lastCartStockRefreshAt = 0

const cartItems = ref([])
const srcBillNo = ref(makeReceiptNo())
const saleDate = ref(new Date())
const remark = ref('')
const submitting = ref(false)
const idempotencyKey = ref(makeIdempotencyKey())
const lastReceipt = ref(null)
const lastSalePrintData = ref(null)
const historyKeyword = ref('')
const historySales = ref([])
const historyLoading = ref(false)
const pendingVoidSale = ref(null)
const voidReason = ref('')
const posStats = ref(defaultPosStats())
const statsLoading = ref(false)
const currentShift = ref(null)
const shiftLoading = ref(false)
const shiftOpeningCashAmount = ref('0.00')
const shiftActualCashAmount = ref('')
const POS_DRAFT_KEY = 'pos_sale_draft_v1'
const POS_AUTO_PRINT_KEY = 'pos_auto_print_sale_v1'
const SALE_PRINT_COMPANY_NAME = '百年达生鲜包装供应链'
const POS_STOCK_QUERY = {
  zone_type: 1,
  picking_only: 1,
}

const paymentMethods = [
  { label: '现金', value: 'CASH' },
  { label: '微信', value: 'WECHAT' },
  { label: '支付宝', value: 'ALIPAY' },
  { label: '银行卡', value: 'BANK_CARD' },
  { label: '其他', value: 'OTHER' },
]
const paymentMethod = ref('WECHAT')
const amountReceived = ref('')
const paymentReferenceNo = ref('')
const autoPrintSale = ref(true)

const {
  lastScan,
  quickScan,
  setScanCallback,
  initScanner,
  unRegisterBroadcast,
} = useBarcodeScanner()

const totalBaseQty = computed(() =>
  cartItems.value.reduce((sum, item) => sum + Number(lineBaseQty(item) || 0), 0)
)
const totalAmount = computed(() =>
  cartItems.value.reduce((sum, item) => sum + Number(lineAmount(item) || 0), 0)
)
const ownerCount = computed(() => {
  const ownerIds = cartItems.value
    .map((item) => item.owner_id)
    .filter((ownerId) => ownerId !== undefined && ownerId !== null && ownerId !== '')
  return new Set(ownerIds).size
})
const paymentMethodLabels = computed(() => paymentMethods.map((method) => method.label))
const paymentMethodIndex = computed(() =>
  Math.max(0, paymentMethods.findIndex((method) => method.value === paymentMethod.value))
)
const selectedPaymentMethod = computed(() => paymentMethods[paymentMethodIndex.value] || paymentMethods[0])
const selectedCustomerName = computed(() =>
  selectedCustomer.value
    ? selectedCustomer.value.name || selectedCustomer.value.code || selectedCustomer.value.id
    : '散客'
)
const saleDateText = computed(() => formatDateTime(saleDate.value))
const receivedAmount = computed(() => Number(amountReceived.value || 0))
const changeAmount = computed(() =>
  paymentMethod.value === 'CASH' ? Math.max(receivedAmount.value - totalAmount.value, 0) : 0
)
const paymentReady = computed(() => {
  if (totalAmount.value <= 0) return false
  if (paymentMethod.value === 'CASH') {
    return receivedAmount.value >= totalAmount.value
  }
  return moneyEqual(receivedAmount.value, totalAmount.value)
})
const shiftSummary = computed(() => currentShift.value?.summary || {})
const canCheckout = computed(() =>
  cartItems.value.length > 0 && paymentReady.value && currentShift.value?.status === 'OPEN'
)
const statsSummary = computed(() => posStats.value?.summary || defaultPosStats().summary)
const statsPayments = computed(() => {
  const rows = Array.isArray(posStats.value?.payments) ? posStats.value.payments : []
  return rows.slice(0, 4)
})

onMounted(() => {
  restoreAutoPrintPreference()
  restoreSaleDraft()
  setScanCallback(handleScan)
  initScanner()
  focusProductInput()
  loadCurrentShift()
  loadPosSaleHistory({ silent: true })
  loadPosStats({ silent: true })
})

onShow(() => {
  focusProductInput()
  refreshCartStock()
  loadPosSaleHistory({ silent: true })
  loadPosStats({ silent: true })
})

onHide(() => {
  saveSaleDraft()
})

onUnmounted(() => {
  saveSaleDraft()
  productInputFocus.value = false
  unRegisterBroadcast()
})

watch(
  () => [
    cartItems.value,
    selectedCustomer.value,
    customerKeyword.value,
    srcBillNo.value,
    saleDate.value,
    remark.value,
    paymentMethod.value,
    amountReceived.value,
    paymentReferenceNo.value,
    idempotencyKey.value,
  ],
  () => saveSaleDraft(),
  { deep: true }
)

watch(totalAmount, () => {
  syncNonCashAmount()
})

function makeReceiptNo() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return [
    'POS',
    d.getFullYear(),
    pad(d.getMonth() + 1),
    pad(d.getDate()),
    pad(d.getHours()),
    pad(d.getMinutes()),
    pad(d.getSeconds()),
  ].join('')
}

function makeIdempotencyKey() {
  return `${makeReceiptNo()}-${Math.random().toString(16).slice(2, 10)}`
}

function formatDateTime(value) {
  const d = value instanceof Date ? value : new Date(value)
  const pad = (n) => String(n).padStart(2, '0')
  return [
    d.getFullYear(),
    pad(d.getMonth() + 1),
    pad(d.getDate()),
  ].join('-') + ` ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function normalizePage(data) {
  if (Array.isArray(data)) return data
  return data && Array.isArray(data.results) ? data.results : []
}

function getStorage() {
  return typeof uni !== 'undefined' ? uni : null
}

function restoreAutoPrintPreference() {
  try {
    const stored = getStorage()?.getStorageSync(POS_AUTO_PRINT_KEY)
    autoPrintSale.value = stored === undefined || stored === null || stored === '' ? true : stored !== false
  } catch (e) {
    autoPrintSale.value = true
  }
}

function saveAutoPrintPreference() {
  try {
    getStorage()?.setStorageSync(POS_AUTO_PRINT_KEY, autoPrintSale.value)
  } catch (e) {
    console.warn('save POS auto print preference failed', e)
  }
}

function onAutoPrintChange(event) {
  const values = Array.isArray(event?.detail?.value) ? event.detail.value : []
  autoPrintSale.value = values.includes('auto')
  saveAutoPrintPreference()
}

function removeSaleDraft() {
  try {
    getStorage()?.removeStorageSync(POS_DRAFT_KEY)
  } catch (e) {
    console.warn('remove POS draft failed', e)
  }
}

function saveSaleDraft() {
  try {
    const storage = getStorage()
    if (!storage) return

    if (!cartItems.value.length) {
      storage.removeStorageSync(POS_DRAFT_KEY)
      return
    }

    const draftItems = JSON.parse(JSON.stringify(cartItems.value))
    const draftCustomer = selectedCustomer.value ? JSON.parse(JSON.stringify(selectedCustomer.value)) : null

    storage.setStorageSync(POS_DRAFT_KEY, {
      saved_at: Date.now(),
      cart_items: draftItems,
      selected_customer: draftCustomer,
      customer_keyword: customerKeyword.value || '',
      src_bill_no: srcBillNo.value || '',
      sale_date: saleDate.value instanceof Date ? saleDate.value.toISOString() : saleDate.value,
      remark: remark.value || '',
      payment_method: paymentMethod.value || 'CASH',
      amount_received: amountReceived.value || '',
      payment_reference_no: paymentReferenceNo.value || '',
      idempotency_key: idempotencyKey.value || '',
    })
  } catch (e) {
    console.warn('save POS draft failed', e)
  }
}

function restoreSaleDraft() {
  try {
    const draft = getStorage()?.getStorageSync(POS_DRAFT_KEY)
    if (!draft || !Array.isArray(draft.cart_items) || !draft.cart_items.length) return

    const savedAt = Number(draft.saved_at || 0)
    if (savedAt && Date.now() - savedAt > 24 * 60 * 60 * 1000) {
      removeSaleDraft()
      return
    }

    cartItems.value = draft.cart_items.map(normalizeDraftCartItem)
    selectedCustomer.value = draft.selected_customer || null
    customerKeyword.value = draft.customer_keyword || ''
    srcBillNo.value = draft.src_bill_no || makeReceiptNo()
    const draftDate = draft.sale_date ? new Date(draft.sale_date) : new Date()
    saleDate.value = Number.isNaN(draftDate.getTime()) ? new Date() : draftDate
    remark.value = draft.remark || ''
    paymentMethod.value = draft.payment_method || 'WECHAT'
    amountReceived.value = draft.amount_received || ''
    paymentReferenceNo.value = draft.payment_reference_no || ''
    idempotencyKey.value = draft.idempotency_key || makeIdempotencyKey()
    syncNonCashAmount()
    setScanFeedback(`已恢复未结账购物车：${cartItems.value.length}项`, 'info')
    refreshCartStock({ force: true, silent: true })
  } catch (e) {
    console.warn('restore POS draft failed', e)
    removeSaleDraft()
  }
}

function normalizeDraftCartItem(item) {
  const options = Array.isArray(item.unit_options) && item.unit_options.length
    ? item.unit_options
    : [{
        kind: 'base',
        package_id: null,
        label: item.base_unit_name || '基本单位',
        multiplier: '1',
        barcode: '',
      }]
  const unitLabels = Array.isArray(item.unit_labels) && item.unit_labels.length
    ? item.unit_labels
    : options.map((option) => option.label || (option.kind === 'base' ? '基本单位' : '包装'))
  const unitIndex = Math.min(Math.max(Number(item.unit_index || 0), 0), Math.max(unitLabels.length - 1, 0))

  return {
    ...item,
    available_qty: item.available_qty || 0,
    qty: String(item.qty || '1'),
    price: priceInputText(item.price),
    unit_options: options,
    unit_labels: unitLabels,
    unit_index: unitIndex,
  }
}

function money(value) {
  const n = Number(value || 0)
  return `¥${Number.isFinite(n) ? n.toFixed(2) : '0.00'}`
}

function priceInputText(value) {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n.toFixed(2) : '0.00'
}

function numberFromValue(value, fallback = 0) {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'number') return Number.isFinite(value) ? value : fallback

  const text = String(value).replace(/,/g, '').trim()
  const matched = text.match(/-?\d+(\.\d+)?/)
  if (!matched) return fallback

  const n = Number(matched[0])
  return Number.isFinite(n) ? n : fallback
}

function stockAvailableQty(source) {
  if (!source || typeof source !== 'object') return 0

  const candidates = [
    source.pos_available_qty,
    source.sale_available_qty,
    source.sales_available_qty,
    source.picking_available_qty,
    source.pick_available_qty,
    source.available_qty_display,
    source.available_qty,
    source.available,
  ]

  for (const value of candidates) {
    if (value !== undefined && value !== null && value !== '') {
      return Math.max(numberFromValue(value, 0), 0)
    }
  }

  return 0
}

function paymentMethodName(value) {
  return paymentMethods.find((method) => method.value === value)?.label || value || '-'
}

function plainMoney(value) {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n.toFixed(2) : '0.00'
}

function moneyEqual(a, b) {
  return Math.abs(Number(a || 0) - Number(b || 0)) < 0.005
}

function syncNonCashAmount() {
  if (paymentMethod.value !== 'CASH') {
    amountReceived.value = plainMoney(totalAmount.value)
  }
}

function confirmDialog({ title, content, confirmText = '确定', cancelText = '取消' }) {
  return new Promise((resolve) => {
    uni.showModal({
      title,
      content,
      confirmText,
      cancelText,
      success: (res) => resolve(!!res.confirm),
      fail: () => resolve(false),
    })
  })
}

function errorMessage(error, fallback = '操作失败') {
  if (!error) return fallback
  if (typeof error === 'string') return error
  if (typeof error.message === 'string' && error.message) return error.message
  const data = error.data || error.response || error
  if (typeof data === 'string') return data
  if (Array.isArray(data)) return data[0] || fallback
  if (typeof data.detail === 'string') return data.detail
  if (Array.isArray(data.detail) && data.detail.length) return data.detail[0]
  for (const key in data) {
    const value = data[key]
    if (Array.isArray(value) && value.length) return value[0]
    if (typeof value === 'string' && value) return value
  }
  return fallback
}

function showPosError(error, fallback = 'POS 操作失败') {
  const message = errorMessage(error, fallback)
  setScanFeedback(message, 'error')
  uni.showModal({
    title: '操作失败',
    content: message,
    showCancel: false,
  })
}

function plainQty(value, context) {
  const n = Number(value || 0)
  if (!Number.isFinite(n)) return '0'
  if (isCountItem(context) || Number.isInteger(n)) return String(Math.trunc(n))
  return String(Number(n.toFixed(3)))
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function dateOnly(value) {
  if (!value) return formatDateTime(new Date()).slice(0, 10)
  const text = String(value)
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) return text.slice(0, 10)
  return formatDateTime(value).slice(0, 10)
}

function amountToChinese(value) {
  const digits = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖']
  const units = ['', '拾', '佰', '仟']
  const groups = ['', '万', '亿']
  const n = Math.round(Number(value || 0) * 100)
  if (!Number.isFinite(n) || n <= 0) return '零元整'

  const integer = Math.floor(n / 100)
  const jiao = Math.floor((n % 100) / 10)
  const fen = n % 10

  function sectionToChinese(section) {
    let str = ''
    let zero = false
    for (let i = 0; i < 4; i++) {
      const d = section % 10
      if (d === 0) {
        if (str) zero = true
      } else {
        if (zero) {
          str = digits[0] + str
          zero = false
        }
        str = digits[d] + units[i] + str
      }
      section = Math.floor(section / 10)
    }
    return str
  }

  let intText = ''
  let rest = integer
  let groupIndex = 0
  let needZero = false
  while (rest > 0) {
    const section = rest % 10000
    if (section === 0) {
      needZero = !!intText
    } else {
      let sectionText = sectionToChinese(section) + groups[groupIndex]
      if (needZero || (section < 1000 && intText)) {
        sectionText = digits[0] + sectionText
      }
      intText = sectionText + intText
      needZero = false
    }
    rest = Math.floor(rest / 10000)
    groupIndex += 1
  }

  let result = `${intText || digits[0]}元`
  if (!jiao && !fen) return `${result}整`
  if (jiao) result += `${digits[jiao]}角`
  if (fen) result += `${digits[fen]}分`
  return result
}

function buildSalePrintData(response = {}) {
  const receipt = response.receipt || {}
  const sale = response.sale || (response.sale_no || response.id ? response : {})
  const payment = receipt.payment || response.payment || sale.payment || {}
  const orders = Array.isArray(response.orders)
    ? response.orders
    : Array.isArray(receipt.orders)
      ? receipt.orders
      : []
  const customer = response.customer || receipt.customer || (cartItems.value.length ? selectedCustomer.value || {} : {})
  const receiptLines = Array.isArray(receipt.lines)
    ? receipt.lines
    : Array.isArray(response.lines)
      ? response.lines
      : []
  const lines = cartItems.value.length
    ? cartItems.value.map((item, index) => {
        const qty = Number(lineBaseQty(item) || 0)
        const price = Number(item.price || 0)
        const amount = Number(lineAmount(item) || 0)
        const unit = item.base_unit_name || item.unit_labels?.[item.unit_index] || ''
        return {
          index: index + 1,
          name: item.name || item.code || item.sku || '',
          code: item.code || item.sku || '',
          spec: item.spec || item.product_spec || selectedUnit(item).label || '',
          unit,
          qty,
          qtyText: plainQty(qty, item),
          price,
          priceText: plainMoney(price),
          amount,
          amountText: plainMoney(amount),
          remark: '',
          locationCode: item.location_code || '',
        }
      })
    : receiptLines.map((line, index) => {
        const qty = numberFromValue(line.qty, 0)
        const price = numberFromValue(line.price, 0)
        const amount = numberFromValue(line.amount, qty * price)
        return {
          index: index + 1,
          name: line.name || line.product_name || line.code || line.product_code || '',
          code: line.code || line.product_code || '',
          spec: line.spec || '',
          unit: line.unit || '',
          qty,
          qtyText: plainQty(qty),
          price,
          priceText: plainMoney(price),
          amount,
          amountText: plainMoney(amount),
          remark: '',
          locationCode: line.location_code || '',
        }
      })
  const totalQty = lines.reduce((sum, line) => sum + Number(line.qty || 0), 0)
  const total = Number(receipt.total_amount ?? sale.total_amount ?? totalAmount.value ?? 0)
  const amountReceivedValue = numberFromValue(payment.amount_received ?? receivedAmount.value, 0)
  const changeValue = numberFromValue(payment.change_amount ?? changeAmount.value, 0)
  const billNo =
    sale.sale_no ||
    receipt.sale_no ||
    receipt.src_bill_no ||
    srcBillNo.value ||
    orders[0]?.order_no ||
    orders[0]?.id ||
    ''

  return {
    companyName: SALE_PRINT_COMPANY_NAME,
    title: '销售单',
    billNo,
    billDate: dateOnly(sale.created_at || receipt.created_at || saleDate.value),
    customerName: customer.name || customer.code || '散客',
    customerAddress: customer.address || customer.full_address || '',
    customerPhone: customer.phone || customer.mobile || '',
    lines,
    totalQty,
    totalQtyText: plainQty(totalQty),
    totalAmount: total,
    totalAmountText: plainMoney(total),
    totalAmountChinese: amountToChinese(total),
    amountReceived: amountReceivedValue,
    amountReceivedText: plainMoney(amountReceivedValue),
    changeAmount: changeValue,
    changeAmountText: plainMoney(changeValue),
    paymentMethod: paymentMethodName(payment.method || paymentMethod.value),
    remark: receipt.remark || sale.remark || remark.value || '',
    orderNos: orders.map((order) => order.order_no || order.id).filter(Boolean).join('、'),
  }
}

function buildSalePrintHtml(data) {
  const rows = data.lines.map((line) => `
    <tr>
      <td class="name">${escapeHtml(line.name)}</td>
      <td>${escapeHtml(line.spec)}</td>
      <td>${escapeHtml(line.unit)}</td>
      <td class="num">${escapeHtml(line.qtyText)}</td>
      <td class="num">${escapeHtml(line.priceText)}</td>
      <td class="num">${escapeHtml(line.amountText)}</td>
      <td>${escapeHtml(line.remark)}</td>
      <td>${escapeHtml(line.locationCode)}</td>
    </tr>
  `).join('')

  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(data.companyName)}${escapeHtml(data.title)}</title>
  <style>
    @page { size: A4 landscape; margin: 8mm; }
    * { box-sizing: border-box; }
    body { margin: 0; color: #111; font-family: SimSun, "Microsoft YaHei", Arial, sans-serif; font-size: 14px; }
    .sheet { width: 100%; padding: 4px 6px; }
    .company { text-align: center; font-size: 28px; font-weight: 700; line-height: 1.1; }
    .title { text-align: center; font-size: 20px; line-height: 1.2; margin-bottom: 4px; }
    .meta { display: grid; grid-template-columns: 1fr 1fr 1.2fr; gap: 12px; font-size: 16px; line-height: 1.5; margin-bottom: 6px; }
    .meta .wide { grid-column: 1 / 3; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 15px; }
    th, td { border: 1px solid #111; padding: 3px 5px; line-height: 1.2; vertical-align: middle; word-break: break-all; }
    th { text-align: center; font-weight: 400; font-size: 16px; }
    .name { text-align: left; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .summary-name { text-align: left; }
    .footer { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; margin-top: 12px; font-size: 16px; line-height: 1.6; }
    .full { grid-column: 1 / -1; }
    .note { margin-top: 4px; font-size: 14px; }
  </style>
</head>
<body>
  <div class="sheet">
    <div class="company">${escapeHtml(data.companyName)}</div>
    <div class="title">${escapeHtml(data.title)}</div>
    <div class="meta">
      <div>客户名称：${escapeHtml(data.customerName)}</div>
      <div>单据日期：${escapeHtml(data.billDate)}</div>
      <div>单据编号：${escapeHtml(data.billNo)}</div>
      <div class="wide">客户地址：${escapeHtml(data.customerAddress)}</div>
      <div>客户电话：${escapeHtml(data.customerPhone)}</div>
    </div>
    <table>
      <colgroup>
        <col style="width: 39%" />
        <col style="width: 12%" />
        <col style="width: 6%" />
        <col style="width: 7%" />
        <col style="width: 8%" />
        <col style="width: 10%" />
        <col style="width: 8%" />
        <col style="width: 10%" />
      </colgroup>
      <thead>
        <tr>
          <th>商品名称</th>
          <th>规格</th>
          <th>单位</th>
          <th>数量</th>
          <th>单价</th>
          <th>金额</th>
          <th>备注</th>
          <th>库位</th>
        </tr>
      </thead>
      <tbody>
        ${rows}
        <tr>
          <td colspan="3" class="summary-name">本页小计</td>
          <td class="num">${escapeHtml(data.totalQtyText)}</td>
          <td></td>
          <td class="num">${escapeHtml(data.totalAmountText)}</td>
          <td colspan="2"></td>
        </tr>
        <tr>
          <td colspan="3" class="summary-name">合计：${escapeHtml(data.totalAmountChinese)}</td>
          <td class="num">${escapeHtml(data.totalQtyText)}</td>
          <td></td>
          <td class="num">${escapeHtml(data.totalAmountText)}</td>
          <td colspan="2"></td>
        </tr>
      </tbody>
    </table>
    <div class="footer">
      <div>本单应收：${escapeHtml(data.totalAmountText)}</div>
      <div>本单实收：${escapeHtml(data.amountReceivedText)}</div>
      <div>本单找零：${escapeHtml(data.changeAmountText)}</div>
      <div>支付方式：${escapeHtml(data.paymentMethod)}</div>
      <div class="full">备注：${escapeHtml(data.remark || '无')}</div>
      <div class="full note">销售出库单：${escapeHtml(data.orderNos || '-')}</div>
    </div>
  </div>
</body>
</html>`
}

function openSalePrintWindow() {
  if (typeof window === 'undefined' || typeof window.open !== 'function') return null
  try {
    return window.open('', '_blank', 'width=1200,height=800')
  } catch (e) {
    return null
  }
}

function printSaleDocument(data, preparedWindow = null) {
  const html = buildSalePrintHtml(data)
  const win = preparedWindow && !preparedWindow.closed ? preparedWindow : openSalePrintWindow()
  if (!win) {
    uni.showToast({ title: '浏览器阻止打印窗口，请手动打印销售单', icon: 'none' })
    return false
  }

  win.document.open()
  win.document.write(html)
  win.document.close()
  setTimeout(() => {
    try {
      win.focus()
      win.print()
    } catch (e) {
      console.warn('print sale document failed', e)
    }
  }, 300)
  return true
}

function printLastSale() {
  if (!lastSalePrintData.value) {
    uni.showToast({ title: '没有可打印的销售单', icon: 'none' })
    return
  }
  printSaleDocument(lastSalePrintData.value)
}

function normalizePosSaleRows(data) {
  if (Array.isArray(data)) return data
  return data && Array.isArray(data.results) ? data.results : []
}

function saleDisplayNo(sale = {}) {
  return sale.src_bill_no || sale.sale_no || String(sale.id || '')
}

function saleStatusText(status) {
  if (status === 'VOIDED') return '已作废'
  if (status === 'COMPLETED') return '已完成'
  return status || '-'
}

function isVoidedSale(sale = {}) {
  return sale.status === 'VOIDED'
}

function salePaymentMethod(sale = {}) {
  return paymentMethodName(sale.payment?.method || sale.receipt?.payment?.method)
}

function saleCreatedText(sale = {}) {
  return sale.created_at ? formatDateTime(sale.created_at) : '-'
}

function saleOrderCount(sale = {}) {
  const orders = Array.isArray(sale.orders)
    ? sale.orders
    : Array.isArray(sale.receipt?.orders)
      ? sale.receipt.orders
      : []
  return orders.length
}

function orderNosText(orders = []) {
  if (!Array.isArray(orders)) return ''
  return orders.map((order) => order.order_no || order.id).filter(Boolean).join('、')
}

async function loadPosSaleHistory(options = {}) {
  if (historyLoading.value && !options.force) return
  historyLoading.value = true
  try {
    const res = await api.posSales({
      search: historyKeyword.value || '',
      page: 1,
      page_size: 10,
    })
    historySales.value = normalizePosSaleRows(res)
  } catch (e) {
    if (!options.silent) {
      showPosError(e, '销售历史加载失败')
    }
  } finally {
    historyLoading.value = false
  }
}

function defaultPosStats() {
  return {
    summary: {
      sale_count: 0,
      completed_count: 0,
      voided_count: 0,
      gross_amount: '0.00',
      net_amount: '0.00',
      voided_amount: '0.00',
    },
    payments: [],
    owners: [],
    products: [],
    cashiers: [],
  }
}

async function loadPosStats(options = {}) {
  if (statsLoading.value && !options.force) return
  statsLoading.value = true
  try {
    const today = dateOnly(new Date())
    const res = await api.posStats({
      start_date: today,
      end_date: today,
      top_n: 5,
    })
    posStats.value = res || defaultPosStats()
  } catch (e) {
    if (!options.silent) {
      showPosError(e, 'POS 统计加载失败')
    }
  } finally {
    statsLoading.value = false
  }
}

function shiftStatusText(status) {
  if (status === 'OPEN') return '进行中'
  if (status === 'CLOSED') return '已交班'
  if (status === 'REOPENED') return '已重开'
  return status || '-'
}

function authHeader() {
  try {
    const token = uni.getStorageSync('access') || ''
    return token ? { Authorization: `Bearer ${token}` } : {}
  } catch (e) {
    return {}
  }
}

function downloadExcel(url, filename = 'pos.xlsx') {
  if (typeof uni.downloadFile !== 'function') {
    if (typeof window !== 'undefined' && window.open) {
      window.open(url, '_blank')
    }
    return
  }
  uni.downloadFile({
    url,
    header: authHeader(),
    success: (res) => {
      if (res.statusCode && res.statusCode !== 200) {
        uni.showToast({ title: '导出失败', icon: 'none' })
        return
      }
      const filePath = res.tempFilePath
      if (typeof uni.openDocument === 'function') {
        uni.openDocument({
          filePath,
          fileType: 'xlsx',
          showMenu: true,
          fail: () => {
            uni.showToast({ title: `${filename} 已下载`, icon: 'none' })
          },
        })
      } else {
        uni.showToast({ title: `${filename} 已下载`, icon: 'none' })
      }
    },
    fail: () => {
      uni.showToast({ title: '导出失败', icon: 'none' })
    },
  })
}

function exportTodayStats() {
  const today = dateOnly(new Date())
  downloadExcel(api.posStatsExport({ start_date: today, end_date: today, top_n: 50 }), 'pos-stats.xlsx')
}

function exportCurrentShift() {
  if (!currentShift.value?.id) return
  downloadExcel(api.posShiftExportUrl(currentShift.value.id), `${currentShift.value.shift_no || 'pos-shift'}.xlsx`)
}

async function loadCurrentShift() {
  if (shiftLoading.value) return
  shiftLoading.value = true
  try {
    const res = await api.posShiftCurrent()
    currentShift.value = res.shift || null
    if (currentShift.value?.summary?.expected_cash_amount) {
      shiftActualCashAmount.value = currentShift.value.summary.expected_cash_amount
    }
  } catch (e) {
    showPosError(e, '班次加载失败')
  } finally {
    shiftLoading.value = false
  }
}

async function openCurrentShift() {
  if (shiftLoading.value) return
  shiftLoading.value = true
  try {
    const res = await api.posShiftOpen({
      opening_cash_amount: shiftOpeningCashAmount.value || '0.00',
    })
    currentShift.value = res.shift || null
    shiftActualCashAmount.value = currentShift.value?.summary?.expected_cash_amount || ''
    uni.showToast({ title: '开班成功', icon: 'none' })
  } catch (e) {
    showPosError(e, '开班失败')
  } finally {
    shiftLoading.value = false
  }
}

async function closeCurrentShift() {
  if (!currentShift.value?.id || shiftLoading.value) return
  const confirmed = await confirmDialog({
    title: '确认交班',
    content: `现金应点 ${money(shiftSummary.value.expected_cash_amount)}，确认交班？`,
    confirmText: '交班',
  })
  if (!confirmed) return
  shiftLoading.value = true
  try {
    const res = await api.posShiftClose(currentShift.value.id, {
      actual_cash_amount: shiftActualCashAmount.value || shiftSummary.value.expected_cash_amount || '0.00',
      remark: '',
    })
    currentShift.value = res.shift || null
    uni.showToast({ title: '交班完成', icon: 'none' })
    loadPosStats({ force: true, silent: true })
    loadPosSaleHistory({ force: true, silent: true })
  } catch (e) {
    showPosError(e, '交班失败')
  } finally {
    shiftLoading.value = false
  }
}

async function reprintSale(sale) {
  if (!sale?.id) return
  try {
    const detail = await api.posSaleDetail(sale.id)
    const printData = buildSalePrintData(detail)
    lastSalePrintData.value = printData
    lastReceipt.value = detail.receipt || lastReceipt.value
    printSaleDocument(printData)
  } catch (e) {
    showPosError(e, '重打销售单失败')
  }
}

function startVoidSale(sale) {
  if (!sale || isVoidedSale(sale)) return
  pendingVoidSale.value = sale
  voidReason.value = ''
}

function cancelVoidSale() {
  pendingVoidSale.value = null
  voidReason.value = ''
}

async function confirmVoidSale() {
  const sale = pendingVoidSale.value
  const reason = (voidReason.value || '').trim()
  if (!sale?.id) return
  if (!reason) {
    uni.showToast({ title: '请输入作废原因', icon: 'none' })
    return
  }
  const confirmed = await confirmDialog({
    title: '确认作废',
    content: `确定作废 ${saleDisplayNo(sale)}？库存将按原扣减明细恢复。`,
    confirmText: '作废',
  })
  if (!confirmed) return

  try {
    const res = await api.posSaleVoid(sale.id, { reason })
    cancelVoidSale()
    uni.showToast({ title: '已作废并恢复库存', icon: 'none' })
    const receipt = res.receipt || null
    if (receipt && lastReceipt.value && saleDisplayNo(lastReceipt.value) === saleDisplayNo(sale)) {
      lastReceipt.value = receipt
    }
    await loadPosSaleHistory({ force: true, silent: true })
    await loadPosStats({ force: true, silent: true })
  } catch (e) {
    showPosError(e, '作废失败')
  }
}

function qtyText(value, context) {
  const n = Number(value || 0)
  if (!Number.isFinite(n)) return isCountItem(context) ? '0' : '0.000'
  if (isCountItem(context) || Number.isInteger(n)) return String(Math.trunc(n))
  return n.toFixed(3)
}

function toPositiveNumber(value, fallback = 1) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n : fallback
}

function explicitIntegerQty(source) {
  if (!source || typeof source !== 'object') return null

  if (source.is_count === true || source.integer_qty === true || source.qty_integer === true) return true
  if (source.allow_decimal === false || source.allow_fraction === false) return true
  if (source.is_decimal === true || source.allow_decimal === true || source.allow_fraction === true) return false

  const decimalPlaces = source.decimal_places ?? source.qty_decimal_places
  if (decimalPlaces !== undefined && decimalPlaces !== null && decimalPlaces !== '') {
    return Number(decimalPlaces) <= 0
  }

  return null
}

function unitNameOf(context) {
  if (!context || typeof context !== 'object') return ''
  const unit = Array.isArray(context.unit_options) ? selectedUnit(context) : null
  return String(
    context.base_unit_name ||
    context.base_unit?.name ||
    context.base_unit?.code ||
    unit?.label ||
    unit?.name ||
    unit?.code ||
    ''
  ).trim().toLowerCase()
}

function isCountUnitName(name) {
  if (!name) return false
  const countUnits = ['个', '件', '只', '支', '瓶', '盒', '箱', '包', '袋', '提', '条', '张', '卷', '罐', '听', '桶', '套', '枚', '把', '双', '台', '部', '本', '片', '颗', '粒', 'pcs', 'pc', 'ea', 'each']
  return countUnits.some((unit) => name === unit || name.endsWith(unit))
}

function isCountItem(context) {
  if (!context || typeof context !== 'object') return false
  const unit = Array.isArray(context.unit_options) ? selectedUnit(context) : null
  const explicit = [context, context.base_unit, unit]
    .map(explicitIntegerQty)
    .find((value) => value !== null)

  if (explicit !== undefined) return explicit
  return isCountUnitName(unitNameOf(context))
}

function formatSaleQty(value, context) {
  const n = toPositiveNumber(value, isCountItem(context) ? 1 : 0.001)
  return isCountItem(context) ? String(Math.max(Math.floor(n), 1)) : String(Math.max(n, 0.001))
}

function setScanFeedback(message, type = 'info') {
  scanFeedbackMessage.value = message || ''
  scanFeedbackType.value = type
}

async function searchCustomers() {
  customerLoading.value = true
  try {
    const res = await api.customers(customerKeyword.value || '', 1)
    customers.value = normalizePage(res)
    if (!customers.value.length) {
      uni.showToast({ title: '未找到客户', icon: 'none' })
    }
  } catch (e) {
    console.error('search customers failed', e)
  } finally {
    customerLoading.value = false
  }
}

function selectCustomer(customer) {
  selectedCustomer.value = customer
}

function handleScan(code) {
  enqueueProductLookup(code, { exactOnly: true, clearInput: true })
}

async function handleProductConfirm(event) {
  const eventValue = getEventValue(event)
  if (eventValue) {
    productKeyword.value = eventValue
  }

  const keyword = await waitKeywordStable(() => productKeyword.value, {
    interval: 50,
    stableTimes: 2,
    maxWait: 500
  })

  enqueueProductLookup(keyword, { clearInput: true })
}

function triggerQuickScan() {
  quickScan()
  focusProductInput()
}

function enqueueProductLookup(keyword, options = {}) {
  const value = String(keyword || '').trim()
  if (!value) {
    focusProductInput()
    return
  }

  const now = Date.now()
  if (lastProductLookup.keyword === value && now - lastProductLookup.time < 120) {
    if (options.clearInput !== false) {
      productKeyword.value = ''
    }
    focusProductInput()
    return
  }
  lastProductLookup = { keyword: value, time: now }

  productLookupQueue.push({
    keyword: value,
    exactOnly: options.exactOnly === true,
  })

  if (options.clearInput !== false) {
    productKeyword.value = ''
  }

  productSearched.value = true
  setScanFeedback(`正在查询：${value}`, 'info')
  focusProductInput()
  drainProductLookupQueue()
}

async function drainProductLookupQueue() {
  if (productLookupRunning) return
  productLookupRunning = true
  productLoading.value = true
  productSearched.value = true

  try {
    while (productLookupQueue.length) {
      const item = productLookupQueue.shift()
      try {
        if (item.exactOnly) {
          await lookupByBarcode(item.keyword)
        } else {
          await lookupProductKeyword(item.keyword)
        }
      } catch (e) {
        console.error('lookup product failed', e)
      }
    }
  } finally {
    productLookupRunning = false
    productLoading.value = false
    focusProductInput()
  }
}

async function fetchProductsByBarcode(barcode) {
  const res = await api.posProducts({ ...POS_STOCK_QUERY, barcode, page: 1, page_size: 20 })
  return normalizePage(res)
}

async function lookupByBarcode(barcode, options = {}) {
  const showNotFound = options.showNotFound !== false
  const rows = await fetchProductsByBarcode(barcode)
  products.value = rows
  if (rows.length === 1) {
    if (addToCart(rows[0])) {
      products.value = []
      productSearched.value = false
    }
    return rows
  }
  if (rows.length > 1) {
    setScanFeedback(`找到 ${rows.length} 个匹配商品，请选择`, 'info')
  }
  if (showNotFound && !rows.length) {
    setScanFeedback(`未找到商品：${barcode}`, 'error')
    uni.showToast({ title: '未找到商品', icon: 'none' })
  }
  return rows
}

function getEventValue(event) {
  return String(event?.detail?.value ?? event?.target?.value ?? '').trim()
}

async function lookupProductKeyword(keyword) {
  const value = String(keyword || '').trim()
  if (!value) {
    products.value = []
    productSearched.value = false
    return []
  }

  const exactRows = await lookupByBarcode(value, { showNotFound: false })
  if (exactRows.length) return exactRows

  const res = await api.posProducts({
    ...POS_STOCK_QUERY,
    search: value,
    page: 1,
    page_size: 20
  })

  products.value = normalizePage(res)

  if (!products.value.length) {
    setScanFeedback(`未找到商品：${value}`, 'error')
    uni.showToast({ title: '未找到商品', icon: 'none' })
  } else {
    setScanFeedback(`找到 ${products.value.length} 个匹配商品，请选择`, 'info')
  }

  return products.value
}

async function searchProducts(event) {
  const eventValue = getEventValue(event)
  if (eventValue) {
    productKeyword.value = eventValue
  }

  const keyword = await waitKeywordStable(() => productKeyword.value, {
    interval: 50,
    stableTimes: 2,
    maxWait: 500
  })

  enqueueProductLookup(keyword, { clearInput: false })
}

function unitOptions(product) {
  const options = Array.isArray(product.unit_options) ? product.unit_options : []
  if (options.length) return options
  return [
    {
      kind: 'base',
      package_id: null,
      label: product.base_unit?.name || product.base_unit?.code || '基本单位',
      multiplier: '1',
      barcode: product.unit_barcode || product.gtin || '',
    },
  ]
}

function productDisplayName(product) {
  return product?.name || product?.sku || product?.code || String(product?.id || '')
}

function hasAvailableStock(product) {
  const available = stockAvailableQty(product)
  return Number.isFinite(available) && available > 0
}

async function refreshCartStock(options = {}) {
  if (cartStockRefreshing || !cartItems.value.length) return

  const now = Date.now()
  if (!options.force && now - lastCartStockRefreshAt < 3000) return

  cartStockRefreshing = true
  lastCartStockRefreshAt = now

  try {
    await Promise.all(cartItems.value.map(async (item) => {
      const keyword = item.code || item.sku || item.name
      if (!keyword) return

      try {
        const rows = normalizePage(await api.posProducts({
          ...POS_STOCK_QUERY,
          search: keyword,
          page: 1,
          page_size: 20,
        }))
        const product = rows.find((row) =>
          row.id === item.product_id ||
          row.code === item.code ||
          (item.sku && row.sku === item.sku)
        )

        if (product) {
          applyProductSnapshotToCartItem(item, product)
          normalizeCartLine(item, { silent: options.silent === true })
        }
      } catch (e) {
        console.warn('refresh POS cart stock failed', item.code || item.product_id, e)
      }
    }))
  } finally {
    cartStockRefreshing = false
  }
}

function applyProductSnapshotToCartItem(item, product) {
  const currentUnitLabel = item.unit_labels?.[item.unit_index]
  const options = unitOptions(product)
  const unitLabels = options.map((option) => option.label || (option.kind === 'base' ? '基本单位' : '包装'))
  const matchedUnitIndex = currentUnitLabel ? unitLabels.findIndex((label) => label === currentUnitLabel) : -1

  item.code = product.code || item.code || ''
  item.owner_id = product.owner_id ?? item.owner_id
  item.sku = product.sku || item.sku || ''
  item.name = productDisplayName(product) || item.name
  item.spec = product.spec || product.specification || product.product_spec || product.package_spec || item.spec || ''
  item.location_code = product.location_code || product.location || product.bin_code || item.location_code || ''
  item.base_unit_name = product.base_unit?.name || product.base_unit?.code || item.base_unit_name || ''
  item.available_qty = stockAvailableQty(product)
  item.min_price = product.min_price
  item.max_discount = product.max_discount
  item.unit_options = options
  item.unit_labels = unitLabels
  item.unit_index = matchedUnitIndex >= 0
    ? matchedUnitIndex
    : Math.min(Math.max(Number(item.unit_index || 0), 0), Math.max(unitLabels.length - 1, 0))
}

function addToCart(product) {
  const available = stockAvailableQty(product)
  const productName = productDisplayName(product)
  const options = unitOptions(product)
  const firstUnit = options[0] || { multiplier: 1 }
  const addBaseQty = toPositiveNumber(firstUnit.multiplier)

  if (!hasAvailableStock(product)) {
    setScanFeedback(`库存不足：${productName}`, 'error')
    uni.showToast({ title: '库存不足，不能加入购物车', icon: 'none' })
    focusProductInput()
    return false
  }

  const exists = cartItems.value.find((item) => item.product_id === product.id)
  if (exists) {
    applyProductSnapshotToCartItem(exists, product)
    const existsAddBaseQty = toPositiveNumber(selectedUnit(exists).multiplier)

    if (Number(lineBaseQty(exists) || 0) + Number(existsAddBaseQty || 0) > Number(exists.available_qty || 0)) {
      setScanFeedback(`库存不足：${exists.name}`, 'error')
      uni.showToast({ title: '数量超过可售库存，不能继续加入', icon: 'none' })
      focusProductInput()
      return false
    }

    exists.qty = String(toPositiveNumber(exists.qty) + 1)
    normalizeCartLine(exists)
    setScanFeedback(`已加入：${exists.name}`, 'success')
    focusProductInput()
    return true
  }

  if (addBaseQty > available) {
    setScanFeedback(`库存不足：${productName}`, 'error')
    uni.showToast({ title: '库存不足，不能加入购物车', icon: 'none' })
    focusProductInput()
    return false
  }

  const unitLabels = options.map((option) => option.label || (option.kind === 'base' ? '基本单位' : '包装'))
  cartItems.value.push({
    product_id: product.id,
    owner_id: product.owner_id,
    code: product.code || '',
    sku: product.sku || '',
    name: productName,
    spec: product.spec || product.specification || product.product_spec || product.package_spec || '',
    location_code: product.location_code || product.location || product.bin_code || '',
    base_unit_name: product.base_unit?.name || product.base_unit?.code || '',
    available_qty: stockAvailableQty(product),
    min_price: product.min_price,
    max_discount: product.max_discount,
    qty: '1',
    price: priceInputText(product.price),
    unit_options: options,
    unit_labels: unitLabels,
    unit_index: 0,
  })
  setScanFeedback(`已加入：${productName}`, 'success')
  uni.showToast({ title: '已加入购物车', icon: 'none' })
  focusProductInput()
  return true
}

function selectedUnit(item) {
  return item.unit_options[item.unit_index] || item.unit_options[0] || { multiplier: 1 }
}

function lineBaseQty(item) {
  const unit = selectedUnit(item)
  const qty = isCountItem(item)
    ? Math.max(Math.floor(toPositiveNumber(item.qty)), 1)
    : toPositiveNumber(item.qty)
  return qty * toPositiveNumber(unit.multiplier)
}

function payloadQty(item) {
  const qty = Number(lineBaseQty(item) || 0)
  return isCountItem(item) ? String(Math.trunc(qty)) : qty.toFixed(3)
}

function lineAmount(item) {
  const price = Number(item.price || 0)
  return Number(lineBaseQty(item) || 0) * (Number.isFinite(price) ? price : 0)
}

function normalizeCartLine(item, options = {}) {
  const available = Number(item.available_qty || 0)
  const unit = selectedUnit(item)
  const multiplier = toPositiveNumber(unit.multiplier)
  let saleQty = toPositiveNumber(item.qty)
  const baseQty = saleQty * multiplier

  if (available > 0 && baseQty > available) {
    saleQty = Math.floor((available / multiplier) * 1000) / 1000
    if (!options.silent) {
      uni.showToast({ title: '数量超过可售库存，已调整', icon: 'none' })
    }
  }

  item.qty = formatSaleQty(saleQty, item)
  item.price = priceInputText(item.price)
}

function normalizeLine(index) {
  const item = cartItems.value[index]
  if (item) normalizeCartLine(item)
}

function changeUnit(index, event) {
  const item = cartItems.value[index]
  if (!item) return
  item.unit_index = Number(event.detail.value || 0)
  normalizeCartLine(item)
}

function removeLine(index) {
  cartItems.value.splice(index, 1)
}

function changePaymentMethod(event) {
  const index = Number(event.detail.value || 0)
  paymentMethod.value = paymentMethods[index]?.value || 'CASH'
  syncNonCashAmount()
}

function goBack() {
  let pageCount = 0
  try {
    pageCount = typeof getCurrentPages === 'function' ? getCurrentPages().length : 0
  } catch (e) {
    pageCount = 0
  }

  if (pageCount > 1) {
    uni.navigateBack()
    return
  }

  uni.switchTab({ url: '/pages/index/index' })
}

function confirmResetSale() {
  uni.showModal({
    title: '清空清单',
    content: '确定清空当前购物车和单据信息？',
    confirmText: '清空',
    cancelText: '取消',
    success: (res) => {
      if (res.confirm) {
        resetSale()
      }
    },
  })
}

function resetSale(options = {}) {
  removeSaleDraft()
  selectedCustomer.value = null
  customers.value = []
  customerKeyword.value = ''
  products.value = []
  productKeyword.value = ''
  productSearched.value = false
  setScanFeedback('', 'info')
  productLookupQueue.length = 0
  lastProductLookup = { keyword: '', time: 0 }
  cartItems.value = []
  saleDate.value = new Date()
  srcBillNo.value = makeReceiptNo()
  idempotencyKey.value = makeIdempotencyKey()
  remark.value = ''
  paymentMethod.value = 'WECHAT'
  amountReceived.value = ''
  paymentReferenceNo.value = ''
  if (!options.keepReceipt) {
    lastReceipt.value = null
    lastSalePrintData.value = null
  }
  focusProductInput()
}

async function validateBeforeCheckout() {
  if (currentShift.value?.status !== 'OPEN') {
    uni.showToast({ title: '请先开班后再结账', icon: 'none' })
    return false
  }
  if (!cartItems.value.length) {
    uni.showToast({ title: '购物车不能为空', icon: 'none' })
    return false
  }

  await refreshCartStock({ force: true, silent: true })

  const overStock = cartItems.value.find((item) => Number(lineBaseQty(item)) > Number(item.available_qty || 0))
  if (overStock) {
    uni.showToast({ title: `${overStock.code} 可售库存不足`, icon: 'none' })
    return false
  }
  if (!paymentReady.value) {
    const message = paymentMethod.value === 'CASH'
      ? '实收金额不足'
      : '非现金支付实收必须等于应收'
    uni.showToast({ title: message, icon: 'none' })
    return false
  }
  if (selectedCustomer.value && ownerCount.value > 1) {
    const confirmed = await confirmDialog({
      title: '多货主拆单',
      content: '已选客户仅用于同货主订单，其他货主将自动使用散客客户。是否继续结账？',
      confirmText: '继续',
    })
    if (!confirmed) return false
  }
  return true
}

async function checkout() {
  if (!(await validateBeforeCheckout())) return
  submitting.value = true
  const preparedPrintWindow = autoPrintSale.value ? openSalePrintWindow() : null
  try {
    syncNonCashAmount()
    const payload = {
      src_bill_no: srcBillNo.value || '',
      idempotency_key: idempotencyKey.value || '',
      stock_zone_type: POS_STOCK_QUERY.zone_type,
      remark: remark.value || '',
      payment: {
        method: paymentMethod.value,
        amount_received: Number(receivedAmount.value || 0).toFixed(2),
        reference_no: paymentReferenceNo.value || '',
      },
      items: cartItems.value.map((item) => ({
        product_id: item.product_id,
        qty: payloadQty(item),
        price: Number(item.price || 0).toFixed(2),
      })),
    }
    if (selectedCustomer.value) {
      payload.customer_id = selectedCustomer.value.id
    }
    const res = await api.posCheckout(payload)
    const orders = Array.isArray(res.orders) ? res.orders : []
    const printData = buildSalePrintData(res)
    lastSalePrintData.value = printData
    lastReceipt.value = res.receipt || {
      sale_no: printData.billNo,
      src_bill_no: printData.billNo,
      total_amount: printData.totalAmount,
      payment: {
        method: paymentMethod.value,
        amount_received: printData.amountReceived,
        change_amount: printData.changeAmount,
      },
      orders,
    }
    const msg =
      orders.length > 1
        ? `结账成功：已生成${orders.length}张销售出库单`
        : `结账成功：${res.sale?.sale_no || orders[0]?.order_no || orders[0]?.id || ''}`
    uni.showToast({ title: msg, icon: 'none' })
    if (autoPrintSale.value) {
      printSaleDocument(printData, preparedPrintWindow)
    }
    resetSale({ keepReceipt: true })
    loadPosSaleHistory({ force: true, silent: true })
    loadPosStats({ force: true, silent: true })
    loadCurrentShift()
  } catch (e) {
    if (preparedPrintWindow && !preparedPrintWindow.closed) {
      preparedPrintWindow.close()
    }
    showPosError(e, '结账失败')
    console.error('pos checkout failed', e)
  } finally {
    submitting.value = false
  }
}


function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function waitKeywordStable(getValue, options = {}) {
  const interval = options.interval || 50       // 每 50ms 检查一次
  const stableTimes = options.stableTimes || 2  // 连续 2 次不变，认为稳定
  const maxWait = options.maxWait || 500        // 最多等 500ms

  let lastValue = String(getValue() || '').trim()
  let sameCount = 0
  let waited = 0

  while (waited < maxWait) {
    await sleep(interval)
    waited += interval

    const currentValue = String(getValue() || '').trim()

    if (currentValue === lastValue) {
      sameCount++
      if (sameCount >= stableTimes) {
        return currentValue
      }
    } else {
      lastValue = currentValue
      sameCount = 0
    }
  }

  return String(getValue() || '').trim()
}

function onProductInput(event) {
  productKeyword.value = String(event.detail.value || '').trim()
}

function focusProductInput(delay = 80) {
  productInputFocus.value = false
  nextTick(() => {
    setTimeout(() => {
      productInputFocus.value = true
    }, delay)
  })
}

</script>

<style scoped>
.pos-page {
  min-height: 100vh;
  height: 100vh;
  background: #f4f6f8;
  padding: 6rpx;
  box-sizing: border-box;
  overflow: hidden;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 42rpx;
  margin-bottom: 6rpx;
  box-sizing: border-box;
}

.nav-back,
.nav-spacer {
  flex: 0 0 46rpx;
  width: 46rpx;
  height: 42rpx;
  box-sizing: border-box;
}

.nav-back {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #172033;
  background: transparent;
  border: 0;
  font-size: 34rpx;
  line-height: 42rpx;
  padding: 0;
}

.title {
  flex: 1;
  color: #172033;
  font-size: 28rpx;
  font-weight: 700;
  line-height: 42rpx;
  text-align: center;
}

.subtitle {
  display: none;
  color: #667085;
  font-size: 21rpx;
  margin-top: 0;
  margin-left: 16rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section {
  background: #fff;
  border: 1rpx solid #e6eaf0;
  border-radius: 8rpx;
  padding: 10rpx;
  margin-bottom: 8rpx;
}

.pos-toolbar {
  padding: 8rpx 10rpx;
  margin-bottom: 0;
}

.shift-section {
  margin: 8rpx 10rpx;
}

.shift-refresh-btn {
  width: 96rpx;
  height: 46rpx;
  font-size: 22rpx;
}

.shift-card,
.shift-open-row {
  display: flex;
  align-items: center;
  gap: 12rpx;
  min-width: 0;
}

.shift-main {
  display: flex;
  flex-direction: column;
  width: 360rpx;
  min-width: 0;
}

.shift-no {
  color: #172033;
  font-size: 26rpx;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shift-meta,
.shift-hint {
  color: #667085;
  font-size: 22rpx;
  line-height: 1.35;
}

.shift-numbers {
  display: flex;
  flex: 1;
  gap: 18rpx;
  min-width: 0;
  color: #344054;
  font-size: 23rpx;
}

.shift-close-row {
  display: flex;
  align-items: center;
  gap: 8rpx;
}

.shift-cash-input {
  width: 180rpx;
  height: 52rpx;
}

.shift-action-btn {
  width: 112rpx;
  height: 52rpx;
  font-size: 22rpx;
}

.scan-line,
.meta-field {
  display: flex;
  align-items: center;
  min-width: 0;
}

.scan-line {
  gap: 6rpx;
}

.toolbar-label {
  flex: 0 0 auto;
  color: #475467;
  font-size: 22rpx;
  font-weight: 600;
  white-space: nowrap;
}

.primary-label {
  color: #172033;
  font-size: 24rpx;
}

.pos-toolbar .scan-input {
  flex: 1 1 520rpx;
  min-width: 0;
  height: 58rpx;
  font-size: 25rpx;
}

.pos-toolbar .scan-btn,
.pos-toolbar .clear-btn {
  flex: 0 0 112rpx;
  width: 112rpx;
  height: 58rpx;
  font-size: 22rpx;
  padding: 0 12rpx;
  white-space: nowrap;
  line-height: 58rpx;
}

.customer-field {
  flex: 0 1 370rpx;
}

.pos-toolbar .meta-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 58rpx;
  font-size: 22rpx;
  padding: 0 10rpx;
  margin-left: 6rpx;
}

.pos-toolbar .meta-btn {
  flex: 0 0 68rpx;
  width: 68rpx;
  height: 58rpx;
  margin-left: 6rpx;
  font-size: 21rpx;
}

.meta-value,
.meta-scan {
  flex: 0 1 auto;
  max-width: 360rpx;
  color: #667085;
  font-size: 21rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pos-toolbar .customer-list,
.pos-toolbar .product-list {
  margin-top: 6rpx;
}

.pos-toolbar .empty-tip {
  padding: 6rpx 0 0;
}

.scan-feedback {
  display: flex;
  align-items: center;
  gap: 12rpx;
  min-height: 36rpx;
  margin-top: 6rpx;
  padding: 0 8rpx;
  border-radius: 6rpx;
  font-size: 22rpx;
  box-sizing: border-box;
}

.scan-feedback.info {
  color: #475467;
  background: #f8fafc;
}

.scan-feedback.success {
  color: #027a48;
  background: #ecfdf3;
}

.scan-feedback.error {
  color: #b42318;
  background: #fff5f5;
}

.scan-code,
.scan-message {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scan-code {
  flex: 0 1 auto;
  max-width: 360rpx;
}

.scan-message {
  flex: 1;
  min-width: 0;
}

.doc-info-row {
  display: flex;
  align-items: center;
  gap: 18rpx;
  min-height: 46rpx;
  padding: 6rpx 10rpx;
}

.doc-info-item {
  display: flex;
  align-items: center;
  min-width: 0;
}

.customer-doc {
  flex: 1 1 auto;
}

.date-doc {
  flex: 0 0 240rpx;
}

.receipt-doc {
  flex: 0 0 420rpx;
}

.doc-label {
  flex: 0 0 auto;
  color: #475467;
  font-size: 22rpx;
  font-weight: 600;
  margin-right: 8rpx;
  white-space: nowrap;
}

.doc-value {
  color: #172033;
  font-size: 23rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-input {
  flex: 1;
  min-width: 0;
  height: 42rpx;
  font-size: 22rpx;
  padding: 0 10rpx;
}

.pos-main {
  display: flex;
  align-items: stretch;
  gap: 10rpx;
  height: calc(100vh - 54rpx);
  min-height: 0;
}

.pos-left {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10rpx;
}

.pos-right {
  flex: 0 0 460rpx;
  width: 460rpx;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10rpx;
  overflow-y: auto;
  padding-right: 2rpx;
  box-sizing: border-box;
}

.customer-panel {
  order: 1;
  margin-bottom: 0;
}

.customer-search-row {
  display: flex;
  align-items: center;
  margin-top: 10rpx;
}

.customer-input {
  flex: 1;
  min-width: 0;
  height: 52rpx;
  font-size: 23rpx;
}

.customer-search-btn {
  flex: 0 0 92rpx;
  width: 92rpx;
  height: 52rpx;
  margin-left: 8rpx;
  font-size: 22rpx;
  white-space: nowrap;
  line-height: 52rpx;
}

.customer-panel .customer-list {
  margin-top: 8rpx;
  max-height: 156rpx;
  overflow-y: auto;
}

.doc-info-stack {
  margin-top: 10rpx;
  border-top: 1rpx solid #edf0f4;
  padding-top: 10rpx;
}

.doc-info-stack .doc-label {
  flex: 0 0 62rpx;
  width: 62rpx;
  margin-right: 8rpx;
  text-align: left;
}

.doc-info-stack .doc-value,
.doc-info-stack .doc-input {
  flex: 1;
  min-width: 0;
}

.doc-info-stack .doc-info-item + .doc-info-item {
  margin-top: 8rpx;
}

.section-head,
.search-row,
.summary-row,
.bill-row,
.cart-controls {
  display: flex;
  align-items: center;
}

.section-head,
.summary-row {
  justify-content: space-between;
}

.section-title {
  color: #172033;
  font-size: 26rpx;
  font-weight: 700;
}

.section-actions {
  display: flex;
  align-items: center;
  gap: 8rpx;
}

.selected-text,
.scan-text {
  color: #667085;
  font-size: 21rpx;
}

.search-row {
  margin-top: 8rpx;
}

.input,
.bill-input,
.payment-input,
.small-input,
.price-input {
  height: 60rpx;
  background: #f8fafc;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  padding: 0 14rpx;
  box-sizing: border-box;
  font-size: 25rpx;
}

.search-row .input {
  height: 56rpx;
  font-size: 24rpx;
}

.flex-input,
.bill-input {
  flex: 1;
}

button {
  margin: 0;
  line-height: 1;
}

.primary-btn,
.ghost-btn,
.danger-btn,
.submit-btn {
  height: 60rpx;
  border-radius: 8rpx;
  font-size: 24rpx;
  display: flex;
  align-items: center;
  justify-content: center;
}

.search-row .primary-btn,
.search-row .ghost-btn {
  height: 56rpx;
  font-size: 24rpx;
}

.primary-btn {
  color: #fff;
  background: #1677ff;
}

.ghost-btn {
  color: #1f2937;
  background: #fff;
  border: 1rpx solid #cfd6e0;
}

.danger-btn {
  color: #b42318;
  background: #fff5f5;
  border: 1rpx solid #ffd0d0;
}

.side-btn {
  width: 96rpx;
  margin-left: 8rpx;
}

.compact-btn {
  width: 78rpx;
  height: 40rpx;
  font-size: 22rpx;
}

.choice-row,
.product-row,
.cart-row {
  display: flex;
  border-top: 1rpx solid #edf0f4;
  padding: 10rpx 0;
}

.choice-row {
  justify-content: space-between;
}

.choice-row.active {
  background: #f0f7ff;
}

.choice-name,
.product-name {
  display: block;
  color: #172033;
  font-size: 26rpx;
  font-weight: 600;
}

.choice-code,
.product-meta {
  display: block;
  color: #667085;
  font-size: 21rpx;
  margin-top: 3rpx;
}

.choice-check {
  color: #1677ff;
  font-size: 24rpx;
}

.product-row {
  align-items: center;
  justify-content: space-between;
}

.product-main,
.cart-info {
  flex: 1;
  min-width: 0;
}

.add-btn {
  width: 100rpx;
  margin-left: 12rpx;
}

.empty-tip,
.empty-cart {
  color: #98a2b3;
  font-size: 23rpx;
  padding: 12rpx 0 4rpx;
  text-align: center;
}

.cart-section {
  flex: 1;
  min-width: 0;
  margin-bottom: 0;
  min-height: 0;
}

 .cart-table {
  width: 100%;
  margin-top: 8rpx;
  overflow-x: auto;
}

.cart-line-row,
.cart-table-head,
.cart-row {
  display: flex !important;
  flex-direction: row !important;
  align-items: center;
  flex-wrap: nowrap !important;
  width: 100%;
  box-sizing: border-box;
}

.cart-table-head {
  height: 38rpx;
  color: #667085;
  font-size: 22rpx;
  border-top: 1rpx solid #edf0f4;
  border-bottom: 1rpx solid #edf0f4;
}

.cart-row {
  min-height: 58rpx;
  padding: 4rpx 0;
  border-top: 1rpx solid #edf0f4;
}

.cart-goods-col {
  flex: 1 1 auto;
  min-width: 0;
  padding-right: 12rpx;
  box-sizing: border-box;
}

.cart-unit-col {
  width: 96rpx;
  flex: 0 0 96rpx;
  margin-left: 8rpx;
  box-sizing: border-box;
}

.cart-qty-col {
  width: 130rpx;
  flex: 0 0 130rpx;
  margin-left: 8rpx;
  text-align: right;
  box-sizing: border-box;
}

.cart-price-col {
  width: 150rpx;
  flex: 0 0 150rpx;
  margin-left: 8rpx;
  text-align: right;
  box-sizing: border-box;
}

.cart-amount-col {
  width: 160rpx;
  flex: 0 0 160rpx;
  margin-left: 8rpx;
  text-align: right;
  box-sizing: border-box;
}

.cart-action-col {
  width: 70rpx;
  flex: 0 0 70rpx;
  margin-left: 8rpx;
  text-align: center;
  box-sizing: border-box;
}

.cart-info {
  display: block;
  overflow: hidden;
}

.cart-goods-name,
.cart-goods-line {
  display: block;
  max-width: 100%;
  color: #172033;
  font-size: 26rpx;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cart-goods-line {
  line-height: 50rpx;
}

.cart-goods-meta {
  display: block;
  max-width: 100%;
  color: #667085;
  font-size: 22rpx;
  margin-left: 0;
  margin-top: 4rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.unit-picker {
  width: 100%;
}

.picker-value {
  width: 100%;
  height: 56rpx;
  line-height: 56rpx;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  background: #fff;
  color: #172033;
  font-size: 24rpx;
  text-align: center;
  box-sizing: border-box;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.small-input {
  width: 100%;
  height: 56rpx;
  min-height: 56rpx;
  text-align: right;
  box-sizing: border-box;
}

.price-wrap {
  width: 100%;
  height: 56rpx;
  background: #f8fafc;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  display: flex;
  align-items: center;
  box-sizing: border-box;
}

.yuan {
  color: #667085;
  font-size: 24rpx;
  padding-left: 10rpx;
}

.price-input {
  flex: 1;
  min-width: 0;
  width: 100%;
  height: 52rpx;
  min-height: 52rpx;
  border: 0;
  background: transparent;
  padding: 0 10rpx 0 6rpx;
  text-align: right;
  box-sizing: border-box;
}

.line-amount {
  display: block;
  color: #172033;
  font-size: 25rpx;
  font-weight: 600;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remove-btn {
  width: 70rpx;
  height: 56rpx;
}

.submit-panel {
  order: 2;
  position: static;
  background: #fff;
  border: 1rpx solid #e6eaf0;
  border-radius: 8rpx;
  padding: 14rpx;
  box-shadow: none;
  box-sizing: border-box;
}

.checkout-row {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 10rpx;
  min-width: 0;
}

.bill-row {
  display: flex;
  align-items: center;
  min-width: 0;
  margin-bottom: 0;
}

.remark-row {
  flex: 0 0 auto;
  order: 5;
}

.payment-row {
  flex: 0 0 auto;
  order: 2;
}

.reference-row {
  flex: 0 0 auto;
  order: 4;
}

.bill-label {
  flex: 0 0 auto;
  width: auto;
  color: #475467;
  font-size: 22rpx;
  margin-right: 6rpx;
  white-space: nowrap;
}

.payment-picker {
  flex: 0 0 136rpx;
  width: 136rpx;
  margin-right: 8rpx;
}

.payment-value {
  height: 56rpx;
  line-height: 56rpx;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  background: #fff;
  color: #172033;
  font-size: 22rpx;
  text-align: center;
}

.payment-input {
  flex: 1;
  min-width: 0;
}

.submit-panel .bill-input,
.submit-panel .payment-input {
  height: 56rpx;
  font-size: 24rpx;
  padding: 0 10rpx;
}

.submit-panel .payment-input {
  height: 70rpx;
  color: #172033;
  background: #fffdf7;
  border-color: #f59e0b;
  font-size: 34rpx;
  font-weight: 700;
  text-align: right;
  padding: 0 16rpx;
}

.submit-panel .payment-input[disabled] {
  background: #f8fafc;
  border-color: #d7dde6;
  color: #475467;
}

.receipt-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #475467;
  font-size: 24rpx;
  border-top: 1rpx solid #edf0f4;
  padding-top: 14rpx;
  margin-top: 14rpx;
}

.receipt-section {
  order: 4;
  margin-bottom: 0;
}

.stats-section {
  order: 3;
  margin-bottom: 0;
}

.history-section {
  order: 5;
  margin-bottom: 0;
}

.stats-refresh-btn {
  width: 96rpx;
  height: 46rpx;
  font-size: 22rpx;
}

.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8rpx;
  margin-top: 10rpx;
}

.stats-card {
  min-width: 0;
  background: #f8fafc;
  border: 1rpx solid #e6eaf0;
  border-radius: 8rpx;
  padding: 10rpx;
}

.stats-card.primary {
  background: #ecfdf3;
  border-color: #bbf7d0;
}

.stats-label {
  display: block;
  color: #667085;
  font-size: 20rpx;
  line-height: 1.2;
  margin-bottom: 4rpx;
}

.stats-value {
  display: block;
  color: #172033;
  font-size: 26rpx;
  font-weight: 700;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stats-card.primary .stats-value {
  color: #027a48;
}

.stats-value.danger {
  color: #b42318;
}

.stats-payments {
  margin-top: 10rpx;
  border-top: 1rpx solid #edf0f4;
}

.stats-payment-row {
  display: flex;
  justify-content: space-between;
  gap: 8rpx;
  color: #475467;
  font-size: 21rpx;
  line-height: 1.35;
  padding-top: 8rpx;
}

.stats-payment-row text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stats-payment-row text:last-child {
  flex: 0 0 auto;
  text-align: right;
}

.receipt-print-btn {
  width: 100%;
  height: 50rpx;
  margin-top: 8rpx;
  font-size: 22rpx;
}

.history-refresh-btn {
  width: 96rpx;
  height: 46rpx;
  font-size: 22rpx;
}

.history-search-row {
  display: flex;
  gap: 8rpx;
  margin-top: 10rpx;
}

.history-input {
  flex: 1;
  min-width: 0;
}

.history-search-btn {
  width: 104rpx;
  height: 56rpx;
  font-size: 22rpx;
}

.history-list {
  margin-top: 10rpx;
}

.history-row {
  display: flex;
  align-items: center;
  gap: 10rpx;
  padding: 12rpx 0;
  border-top: 1rpx solid #edf0f4;
}

.history-main {
  flex: 1;
  min-width: 0;
}

.history-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8rpx;
}

.history-no {
  color: #172033;
  font-size: 24rpx;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-status {
  flex: 0 0 auto;
  font-size: 20rpx;
  font-weight: 700;
}

.history-status.completed {
  color: #0f766e;
}

.history-status.voided {
  color: #b42318;
}

.history-meta {
  display: block;
  color: #667085;
  font-size: 21rpx;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-actions {
  display: flex;
  flex-direction: column;
  gap: 6rpx;
  flex: 0 0 90rpx;
}

.history-action-btn {
  width: 90rpx;
  height: 44rpx;
  font-size: 20rpx;
}

.void-panel {
  margin-top: 10rpx;
  padding: 10rpx;
  background: #fff7ed;
  border: 1rpx solid #fed7aa;
  border-radius: 8rpx;
}

.void-title {
  display: block;
  color: #9a3412;
  font-size: 22rpx;
  font-weight: 700;
  margin-bottom: 8rpx;
}

.void-input {
  width: 100%;
  height: 56rpx;
  background: #fff;
}

.void-actions {
  display: flex;
  gap: 8rpx;
  margin-top: 8rpx;
}

.void-action-btn {
  flex: 1;
  height: 48rpx;
  font-size: 22rpx;
}

.receipt-nos {
  flex: 1;
  min-width: 0;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.summary-title {
  display: block;
  color: #b42318;
  font-size: 56rpx;
  font-weight: 700;
  line-height: 1.05;
  text-align: right;
}

.amount-label {
  display: block;
  color: #667085;
  font-size: 22rpx;
  font-weight: 600;
  margin-bottom: 4rpx;
}

.amount-due .amount-label,
.amount-due .summary-title {
  text-align: right;
}

.amount-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8rpx;
}

.amount-box {
  background: #f8fafc;
  border: 1rpx solid #e6eaf0;
  border-radius: 8rpx;
  padding: 10rpx;
}

.amount-value {
  display: block;
  color: #172033;
  font-size: 30rpx;
  font-weight: 700;
}

.change-value {
  color: #b42318;
}

.summary-sub {
  display: flex;
  justify-content: space-between;
  gap: 8rpx;
  color: #667085;
  font-size: 22rpx;
  white-space: nowrap;
}

.owner-warning {
  color: #9a3412;
  background: #fff7ed;
  border: 1rpx solid #fed7aa;
  border-radius: 8rpx;
  padding: 8rpx 10rpx;
  font-size: 22rpx;
  line-height: 1.35;
}

.print-check-group {
  display: block;
}

.print-option {
  display: flex;
  align-items: center;
  color: #475467;
  font-size: 22rpx;
  line-height: 1;
}

.print-option checkbox {
  transform: scale(0.78);
  transform-origin: left center;
  margin-right: 2rpx;
}

.submit-btn {
  width: 100%;
  height: 78rpx;
  color: #fff;
  background: #0f766e;
  font-weight: 700;
  font-size: 32rpx;
}

.summary-row {
  order: 1;
  flex: 0 0 auto;
  min-width: 0;
  margin-left: 0;
  padding-bottom: 8rpx;
  border-bottom: 1rpx solid #edf0f4;
  flex-direction: column;
  align-items: stretch;
  gap: 12rpx;
}

.submit-btn[disabled],
.primary-btn[disabled] {
  opacity: 0.45;
}
</style>

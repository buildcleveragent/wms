<template>
  <view class="pos-page">
    <view class="topbar">
      <view>
        <text class="title">POS收银</text>
        <text class="subtitle">扫码、查价、校验库存并生成销售出库单</text>
      </view>
      <button class="ghost-btn compact-btn" @click="resetSale">清空</button>
    </view>

    <view class="section">
      <view class="section-head">
        <text class="section-title">客户（可选）</text>
        <text class="selected-text" v-if="selectedCustomer">
          {{ selectedCustomer.name || selectedCustomer.code || selectedCustomer.id }}
        </text>
        <text class="selected-text" v-else>未选客户按散客结账</text>
      </view>
      <view class="search-row">
        <input
          class="input flex-input"
          v-model.trim="customerKeyword"
          placeholder="客户名称/编码"
          confirm-type="search"
          @confirm="searchCustomers"
        />
        <button class="primary-btn side-btn" :disabled="customerLoading" @click="searchCustomers">
          搜索
        </button>
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
    </view>

    <view class="section">
      <view class="section-head">
        <text class="section-title">商品</text>
        <text class="scan-text" v-if="lastScan">最近扫码：{{ lastScan }}</text>
      </view>
      <view class="search-row">
        <input
          class="input flex-input"
          v-model.trim="productKeyword"
          placeholder="商品名称/编码/SKU/条码"
          confirm-type="search"
          @confirm="searchProducts"
        />
        <button class="ghost-btn side-btn" @click="quickScan">扫码</button>
        <button class="primary-btn side-btn" :disabled="productLoading" @click="searchProducts">
          搜索
        </button>
      </view>
      <view class="product-list" v-if="products.length">
        <view class="product-row" v-for="product in products" :key="product.id">
          <view class="product-main">
            <text class="product-name">{{ product.name || product.sku || product.code }}</text>
            <text class="product-meta">{{ product.code }} {{ product.sku || '' }}</text>
            <text class="product-meta">
              售价 {{ money(product.price) }} / 可售 {{ qtyText(product.available_qty) }}
            </text>
          </view>
          <button class="primary-btn add-btn" @click="addToCart(product)">加入</button>
        </view>
      </view>
      <view class="empty-tip" v-else-if="productSearched && !productLoading">未找到商品</view>
    </view>

    <view class="section cart-section">
      <view class="section-head">
        <text class="section-title">购物车</text>
        <text class="selected-text">{{ cartItems.length }}项 / {{ money(totalAmount) }}</text>
      </view>

      <view v-if="!cartItems.length" class="empty-cart">扫码或搜索商品后加入购物车</view>

      <view v-for="(item, index) in cartItems" :key="item.product_id" class="cart-row">
        <view class="cart-info">
          <text class="product-name">{{ item.name }}</text>
          <text class="product-meta">
            {{ item.code }} / 可售 {{ qtyText(item.available_qty) }} {{ item.base_unit_name }}
          </text>
          <text class="product-meta">
            基本数量 {{ qtyText(lineBaseQty(item)) }} {{ item.base_unit_name }}
          </text>
        </view>

        <view class="cart-controls">
          <view class="control-field unit-field">
            <text class="field-label">单位</text>
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
          <view class="control-field qty-field">
            <text class="field-label">数量</text>
            <input
              class="small-input"
              type="digit"
              v-model="item.qty"
              @blur="normalizeLine(index)"
            />
          </view>
          <view class="control-field price-field">
            <text class="field-label">单价</text>
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
          <view class="control-field action-field">
            <text class="field-label">操作</text>
            <button class="danger-btn remove-btn" @click="removeLine(index)">删</button>
          </view>
        </view>
      </view>
    </view>

    <view class="submit-panel">
      <view class="bill-row">
        <text class="bill-label">小票号</text>
        <input class="bill-input" v-model.trim="srcBillNo" placeholder="可留空或输入外部单号" />
      </view>
      <view class="bill-row">
        <text class="bill-label">备注</text>
        <input class="bill-input" v-model.trim="remark" placeholder="可选" />
      </view>
      <view class="summary-row">
        <view>
          <text class="summary-title">{{ money(totalAmount) }}</text>
          <text class="summary-sub">合计 {{ qtyText(totalBaseQty) }} 基本数量</text>
        </view>
        <button class="submit-btn" :disabled="!canCheckout || submitting" @click="checkout">
          结账
        </button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/utils/request'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'

const customerKeyword = ref('')
const customers = ref([])
const selectedCustomer = ref(null)
const customerLoading = ref(false)

const productKeyword = ref('')
const products = ref([])
const productLoading = ref(false)
const productSearched = ref(false)

const cartItems = ref([])
const srcBillNo = ref(makeReceiptNo())
const remark = ref('')
const submitting = ref(false)

const {
  lastScan,
  quickScan,
  setScanCallback,
  initScanner,
  unRegisterBroadcast,
} = useBarcodeScanner()

const canCheckout = computed(() => cartItems.value.length > 0)
const totalBaseQty = computed(() =>
  cartItems.value.reduce((sum, item) => sum + Number(lineBaseQty(item) || 0), 0)
)
const totalAmount = computed(() =>
  cartItems.value.reduce((sum, item) => sum + Number(lineBaseQty(item) || 0) * Number(item.price || 0), 0)
)

onMounted(() => {
  setScanCallback(handleScan)
  initScanner()
})

onUnmounted(() => {
  unRegisterBroadcast()
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

function normalizePage(data) {
  if (Array.isArray(data)) return data
  return data && Array.isArray(data.results) ? data.results : []
}

function money(value) {
  const n = Number(value || 0)
  return `¥${n.toFixed(2)}`
}

function qtyText(value) {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n.toFixed(3) : '0.000'
}

function toPositiveNumber(value, fallback = 1) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n : fallback
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

async function handleScan(code) {
  if (!code) return
  productKeyword.value = code
  productLoading.value = true
  productSearched.value = true
  try {
    await lookupByBarcode(code)
  } catch (e) {
    console.error('lookup product failed', e)
  } finally {
    productLoading.value = false
  }
}

async function fetchProductsByBarcode(barcode) {
  const res = await api.posProducts({ barcode, page: 1, page_size: 20 })
  return normalizePage(res)
}

async function lookupByBarcode(barcode, options = {}) {
  const showNotFound = options.showNotFound !== false
  const rows = await fetchProductsByBarcode(barcode)
  products.value = rows
  if (rows.length === 1) {
    addToCart(rows[0])
    products.value = []
    return rows
  }
  if (showNotFound && !rows.length) {
    uni.showToast({ title: '未找到商品', icon: 'none' })
  }
  return rows
}

async function searchProducts() {
  const keyword = productKeyword.value || ''
  productLoading.value = true
  productSearched.value = true
  try {
    const exactRows = keyword ? await lookupByBarcode(keyword, { showNotFound: false }) : []
    if (exactRows.length) return

    const res = await api.posProducts({ search: keyword, page: 1, page_size: 20 })
    products.value = normalizePage(res)
    if (!products.value.length) {
      uni.showToast({ title: '未找到商品', icon: 'none' })
    }
  } catch (e) {
    console.error('search products failed', e)
  } finally {
    productLoading.value = false
  }
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

function addToCart(product) {
  const exists = cartItems.value.find((item) => item.product_id === product.id)
  if (exists) {
    exists.qty = String(toPositiveNumber(exists.qty) + 1)
    normalizeCartLine(exists)
    return
  }

  const options = unitOptions(product)
  const unitLabels = options.map((option) => option.label || (option.kind === 'base' ? '基本单位' : '包装'))
  cartItems.value.push({
    product_id: product.id,
    code: product.code || '',
    sku: product.sku || '',
    name: product.name || product.sku || product.code || String(product.id),
    base_unit_name: product.base_unit?.name || product.base_unit?.code || '',
    available_qty: product.available_qty || 0,
    min_price: product.min_price,
    max_discount: product.max_discount,
    qty: '1',
    price: String(product.price || '0'),
    unit_options: options,
    unit_labels: unitLabels,
    unit_index: 0,
  })
  uni.showToast({ title: '已加入购物车', icon: 'none' })
}

function selectedUnit(item) {
  return item.unit_options[item.unit_index] || item.unit_options[0] || { multiplier: 1 }
}

function lineBaseQty(item) {
  const unit = selectedUnit(item)
  return toPositiveNumber(item.qty) * toPositiveNumber(unit.multiplier)
}

function normalizeCartLine(item) {
  const available = Number(item.available_qty || 0)
  const unit = selectedUnit(item)
  const multiplier = toPositiveNumber(unit.multiplier)
  let saleQty = toPositiveNumber(item.qty)
  const baseQty = saleQty * multiplier

  if (available > 0 && baseQty > available) {
    saleQty = Math.floor((available / multiplier) * 1000) / 1000
    uni.showToast({ title: '数量超过可售库存，已调整', icon: 'none' })
  }

  item.qty = String(Math.max(saleQty, 0.001))
  item.price = String(Math.max(Number(item.price || 0), 0).toFixed(4))
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

function resetSale() {
  selectedCustomer.value = null
  customers.value = []
  customerKeyword.value = ''
  products.value = []
  productKeyword.value = ''
  productSearched.value = false
  cartItems.value = []
  srcBillNo.value = makeReceiptNo()
  remark.value = ''
}

function validateBeforeCheckout() {
  if (!cartItems.value.length) {
    uni.showToast({ title: '购物车不能为空', icon: 'none' })
    return false
  }
  const overStock = cartItems.value.find((item) => Number(lineBaseQty(item)) > Number(item.available_qty || 0))
  if (overStock) {
    uni.showToast({ title: `${overStock.code} 可售库存不足`, icon: 'none' })
    return false
  }
  return true
}

async function checkout() {
  if (!validateBeforeCheckout()) return
  submitting.value = true
  try {
    const payload = {
      src_bill_no: srcBillNo.value || '',
      remark: remark.value || '',
      items: cartItems.value.map((item) => ({
        product_id: item.product_id,
        qty: Number(lineBaseQty(item)).toFixed(3),
        price: Number(item.price || 0).toFixed(4),
      })),
    }
    if (selectedCustomer.value) {
      payload.customer_id = selectedCustomer.value.id
    }
    const res = await api.posCheckout(payload)
    const orders = Array.isArray(res.orders) ? res.orders : []
    const msg =
      orders.length > 1
        ? `结账成功：已生成${orders.length}张销售出库单`
        : `结账成功：${orders[0]?.order_no || orders[0]?.id || res.order_no || res.id || ''}`
    uni.showToast({ title: msg, icon: 'none' })
    resetSale()
  } catch (e) {
    console.error('pos checkout failed', e)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.pos-page {
  min-height: 100vh;
  background: #f4f6f8;
  padding: 18rpx;
  padding-bottom: 260rpx;
  box-sizing: border-box;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18rpx;
}

.title {
  display: block;
  color: #172033;
  font-size: 38rpx;
  font-weight: 700;
}

.subtitle {
  display: block;
  color: #667085;
  font-size: 24rpx;
  margin-top: 6rpx;
}

.section {
  background: #fff;
  border: 1rpx solid #e6eaf0;
  border-radius: 12rpx;
  padding: 18rpx;
  margin-bottom: 16rpx;
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
  font-size: 30rpx;
  font-weight: 700;
}

.selected-text,
.scan-text {
  color: #667085;
  font-size: 23rpx;
}

.search-row {
  margin-top: 14rpx;
}

.input,
.bill-input,
.small-input,
.price-input {
  height: 74rpx;
  background: #f8fafc;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  padding: 0 18rpx;
  box-sizing: border-box;
  font-size: 27rpx;
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
  height: 74rpx;
  border-radius: 8rpx;
  font-size: 26rpx;
  display: flex;
  align-items: center;
  justify-content: center;
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
  width: 112rpx;
  margin-left: 10rpx;
}

.compact-btn {
  width: 104rpx;
}

.choice-row,
.product-row,
.cart-row {
  display: flex;
  border-top: 1rpx solid #edf0f4;
  padding: 16rpx 0;
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
  font-size: 28rpx;
  font-weight: 600;
}

.choice-code,
.product-meta {
  display: block;
  color: #667085;
  font-size: 23rpx;
  margin-top: 6rpx;
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
  font-size: 26rpx;
  padding: 28rpx 0 10rpx;
  text-align: center;
}

.cart-section {
  margin-bottom: 18rpx;
}

.cart-row {
  align-items: flex-start;
  flex-direction: column;
}

.cart-controls {
  width: 100%;
  margin-top: 14rpx;
  align-items: flex-end;
}

.control-field {
  margin-right: 10rpx;
}

.field-label {
  display: block;
  color: #667085;
  font-size: 22rpx;
  margin-bottom: 8rpx;
}

.unit-field {
  width: 180rpx;
}

.qty-field {
  width: 126rpx;
}

.price-field {
  width: 174rpx;
}

.action-field {
  width: 78rpx;
  margin-right: 0;
}

.unit-picker {
  width: 100%;
}

.picker-value {
  height: 70rpx;
  line-height: 70rpx;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  background: #fff;
  color: #172033;
  font-size: 25rpx;
  text-align: center;
}

.small-input {
  width: 100%;
}

.price-wrap {
  height: 74rpx;
  background: #f8fafc;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  display: flex;
  align-items: center;
  box-sizing: border-box;
}

.yuan {
  color: #667085;
  font-size: 26rpx;
  padding-left: 14rpx;
}

.price-input {
  flex: 1;
  height: 70rpx;
  border: 0;
  background: transparent;
  padding-left: 8rpx;
}

.remove-btn {
  width: 78rpx;
}

.submit-panel {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  background: #fff;
  border-top: 1rpx solid #d7dde6;
  padding: 14rpx 18rpx 20rpx;
  box-shadow: 0 -6rpx 24rpx rgba(15, 23, 42, 0.08);
  box-sizing: border-box;
}

.bill-row {
  margin-bottom: 10rpx;
}

.bill-label {
  width: 104rpx;
  color: #475467;
  font-size: 25rpx;
}

.summary-title {
  display: block;
  color: #b42318;
  font-size: 36rpx;
  font-weight: 700;
}

.summary-sub {
  display: block;
  color: #667085;
  font-size: 23rpx;
  margin-top: 4rpx;
}

.submit-btn {
  width: 180rpx;
  color: #fff;
  background: #0f766e;
  font-weight: 700;
}

.submit-btn[disabled],
.primary-btn[disabled] {
  opacity: 0.45;
}
</style>

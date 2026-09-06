<template>
  <view class="pos-page">
    <view class="pos-header">
      <view class="header-button" @click="back"><text>‹</text></view>
      <text class="header-title">手机收银</text>
      <view class="header-button" :class="{ disabled: !canViewHistory }" @click="openHistory">
        <WmsIcon name="history" />
      </view>
    </view>

    <scroll-view class="pos-scroll" scroll-y>
      <view class="pos-content">
        <view class="status-row">
          <view :class="['shift-chip', { open: shiftIsOpen }]" @click="shiftPanelOpen = true">
            <view class="shift-dot" />
            <text>{{ shiftLoading ? '读取班次…' : shiftIsOpen ? `${currentShift.shift_no || '当前班次'} · 已开班` : '未开班 · 点击处理' }}</text>
          </view>
          <text class="stock-scope">库存范围：全仓</text>
        </view>

        <view v-if="pendingCheckout" class="pending-banner">
          <view class="pending-copy">
            <text class="pending-title">上一笔销售结果待确认</text>
            <text>购物车已锁定，将用同一请求编号重试，避免重复销售。</text>
          </view>
          <button class="retry-button" :disabled="submitting" @click="retryCheckout">重试确认</button>
        </view>

        <button class="scan-button" :disabled="submitting || !!pendingCheckout" @click="quickScan">
          <WmsIcon name="scan" />
          <text>{{ scanning ? '正在打开摄像头…' : '扫码商品' }}</text>
        </button>

        <view class="search-box">
          <WmsIcon name="search" />
          <input
            v-model.trim="productKeyword"
            data-testid="pos-product-input"
            :disabled="!!pendingCheckout"
            placeholder="搜索商品名称、编码或条码"
            confirm-type="search"
            @confirm="searchProducts({ reset: true })"
          />
          <text v-if="productKeyword" class="search-clear" @click="clearSearch">清除</text>
        </view>

        <view v-if="scanFeedback" :class="['scan-feedback', feedbackTone]">
          <text>{{ scanFeedback }}</text>
        </view>

        <view v-if="productSearched" class="search-results">
          <view class="result-heading">
            <text>搜索结果</text><text>{{ products.length }} 项</text>
          </view>
          <view v-if="!products.length && !productLoading" class="empty-state">没有找到商品，可修改关键词后重试。</view>
          <view v-for="product in products" :key="product.id" class="product-result" data-testid="pos-product-row">
            <view class="product-main">
              <text class="product-name" data-testid="pos-product-name">{{ productTitle(product) }}</text>
              <text class="product-meta">{{ product.code || product.sku || '-' }} · 全仓可用 {{ qty(product.available_qty) }}</text>
            </view>
            <view class="result-price">{{ money(product.price) }}</view>
            <button class="add-btn" :disabled="Number(product.available_qty || 0) <= 0 || !!pendingCheckout" @click="addProduct(product)">添加</button>
          </view>
          <button v-if="hasMoreProducts" class="load-more" :disabled="productLoading" @click="searchProducts({ reset: false })">
            {{ productLoading ? '加载中…' : '加载更多' }}
          </button>
        </view>

        <view class="cart-heading">
          <text>购物车</text><text>{{ cartItems.length ? `${cartItems.length} 种商品` : '等待扫码' }}</text>
        </view>
        <view v-if="!cartItems.length" class="cart-empty">
          <view class="empty-scan"><WmsIcon name="scan" /></view>
          <text>点击“扫码商品”，从货架旁直接加入购物车</text>
        </view>

        <view v-for="(item, index) in cartItems" :key="cartKey(item)" class="cart-card" data-testid="pos-cart-product">
          <view class="cart-top">
            <view class="product-placeholder"><WmsIcon name="receive" /></view>
            <view class="cart-copy">
              <text class="cart-name">{{ productTitle(item) }}</text>
              <text class="cart-code">{{ item.code || item.sku || '-' }} · 全仓可用 {{ qty(item.available_qty) }}</text>
            </view>
            <text class="cart-amount">{{ money(lineAmount(item)) }}</text>
          </view>
          <view class="cart-controls">
            <button class="quantity-button" :disabled="!!pendingCheckout" @click="decrease(index)">−</button>
            <input class="quantity-input" type="digit" :disabled="!!pendingCheckout" v-model="item.qty" @blur="normalizeQuantity(index)" />
            <button class="quantity-button add" :disabled="!!pendingCheckout" @click="increase(index)">＋</button>
            <picker class="unit-picker" mode="selector" :disabled="!!pendingCheckout" :range="item.unit_options.map((unit) => unit.label)" :value="item.unit_index" @change="changeUnit(index, $event)">
              <view class="unit-value">{{ selectedUnit(item).label }} <text>⌄</text></view>
            </picker>
          </view>
          <view class="price-row">
            <text>基本单位单价</text>
            <strong class="fixed-price">{{ money(item.price) }}</strong>
            <text class="remove-link" @click="removeItem(index)">移除</text>
          </view>
        </view>

        <view class="remark-row">
          <text>销售备注</text>
          <input v-model.trim="remark" :disabled="!!pendingCheckout" placeholder="可选" />
        </view>
        <view class="scroll-spacer" />
      </view>
    </scroll-view>

    <view class="checkout-bar safe-bottom">
      <view class="checkout-total"><text>共 {{ totalPieces }} 件</text><view>合计 <strong>{{ money(totalAmount) }}</strong></view></view>
      <button data-testid="pos-checkout" :disabled="!cartItems.length || submitting || !!pendingCheckout" @click="openPayment">去收款</button>
    </view>

    <view v-if="paymentOpen" class="overlay" @click.self="paymentOpen = false">
      <view class="bottom-sheet safe-bottom">
        <view class="sheet-handle" />
        <text class="sheet-title">确认收款</text>
        <text class="amount-label">应收金额</text>
        <text class="amount-due">{{ money(totalAmount) }}</text>
        <view class="sheet-divider" />
        <text class="form-label">收款方式</text>
        <view class="payment-list">
          <view v-for="method in paymentMethods" :key="method.value" :class="['payment-option', { selected: paymentMethod === method.value }]" @click="selectPayment(method.value)">
            <view :class="['payment-logo', method.value.toLowerCase()]">{{ method.label.slice(0, 1) }}</view>
            <text>{{ method.label }}</text><view class="radio"><view /></view>
          </view>
        </view>
        <view v-if="paymentMethod === 'CASH'" class="cash-row">
          <text>顾客实付</text><view><text>¥</text><input v-model="amountReceived" type="digit" placeholder="0.00" /></view>
        </view>
        <view v-if="paymentMethod === 'CASH'" class="change-row"><text>找零</text><strong>{{ money(changeAmount) }}</strong></view>
        <input v-if="paymentMethod !== 'CASH'" v-model.trim="paymentReference" class="reference-input" placeholder="支付流水号（可选）" />
        <view class="payment-notice">顾客已完成付款后再确认</view>
        <view class="sheet-actions">
          <button class="cancel-button" @click="paymentOpen = false">取消</button>
          <button class="confirm-button" :disabled="!paymentReady || submitting" @click="checkout">{{ submitting ? '正在记录…' : '确认已收款并记录销售' }}</button>
        </view>
      </view>
    </view>

    <view v-if="shiftPanelOpen" class="overlay" @click.self="shiftPanelOpen = false">
      <view class="bottom-sheet compact safe-bottom">
        <view class="sheet-handle" /><text class="sheet-title">{{ shiftIsOpen ? '当前班次' : '开始收银班次' }}</text>
        <template v-if="shiftIsOpen">
          <view class="shift-summary"><text>班次号</text><strong>{{ currentShift.shift_no || '-' }}</strong></view>
          <view class="shift-summary"><text>现金应点</text><strong>{{ money(currentShift.summary?.expected_cash_amount) }}</strong></view>
          <text class="form-label">现金实点金额</text>
          <input v-model="actualCash" class="sheet-input" type="digit" placeholder="0.00" />
          <button class="danger-action" :disabled="shiftLoading" @click="closeShift">确认交班</button>
        </template>
        <template v-else>
          <text class="form-label">开班备用现金</text>
          <input v-model="openingCash" class="sheet-input" type="digit" placeholder="0.00" />
          <button class="confirm-button full" :disabled="shiftLoading" @click="openShift">确认开班</button>
        </template>
      </view>
    </view>

    <view v-if="productChoices.length" class="overlay" @click.self="productChoices = []">
      <view class="bottom-sheet choices safe-bottom">
        <view class="sheet-handle" /><text class="sheet-title">选择匹配商品</text>
        <text class="choice-hint">该条码匹配多个货主或商品，请确认后加入。</text>
        <scroll-view class="choice-list" scroll-y>
          <view v-for="product in productChoices" :key="product.id" class="choice-row" @click="chooseScannedProduct(product)">
            <view><text>{{ productTitle(product) }}</text><text>{{ product.owner_name || `货主 ${product.owner_id || '-'}` }} · {{ product.code || product.sku }}</text></view>
            <text>{{ qty(product.available_qty) }}</text>
          </view>
        </scroll-view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import WmsIcon from '@/components/WmsIcon.vue'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'
import { hasPermission } from '@/utils/mobileAccess'
import {
  POS_PAYMENT_METHODS, addProductToCart, availableQty, buildCheckoutSnapshot,
  cartLineKey, draftStorageKey, finalizeCheckoutSnapshot, lineAmount as calculateLineAmount,
  lineBaseQty, money, normalizeRows, pendingStorageKey, productName, productSpec,
  toNumber, unitOptions, validateCart,
} from '@/utils/posMobile'

const auth = useAuth()
const productKeyword = ref('')
const products = ref([])
const productSearched = ref(false)
const productLoading = ref(false)
const productPage = ref(1)
const hasMoreProducts = ref(false)
const cartItems = ref([])
const scanFeedback = ref('')
const feedbackTone = ref('success')
const productChoices = ref([])
const pendingScanCode = ref('')
const currentShift = ref(null)
const shiftLoading = ref(false)
const shiftPanelOpen = ref(false)
const openingCash = ref('0.00')
const actualCash = ref('')
const paymentOpen = ref(false)
const paymentMethod = ref('WECHAT')
const amountReceived = ref('')
const paymentReference = ref('')
const remark = ref('')
const submitting = ref(false)
const pendingCheckout = ref(null)
const restored = ref(false)

const paymentMethods = POS_PAYMENT_METHODS
const canViewHistory = computed(() => hasPermission(auth, 'pos.view_possale'))
const shiftIsOpen = computed(() => ['OPEN', 'REOPENED'].includes(currentShift.value?.status))
const totalAmount = computed(() => cartItems.value.reduce((sum, item) => sum + calculateLineAmount(item), 0))
const totalPieces = computed(() => cartItems.value.reduce((sum, item) => sum + Math.max(toNumber(item.qty, 0), 0), 0))
const changeAmount = computed(() => Math.max(toNumber(amountReceived.value, 0) - totalAmount.value, 0))
const paymentReady = computed(() => paymentMethod.value !== 'CASH' || toNumber(amountReceived.value, 0) >= totalAmount.value)

function productTitle(product) { return [productName(product), productSpec(product)].filter(Boolean).join('　') }
function qty(value) { const n = toNumber(value, 0); return Number.isInteger(n) ? String(n) : n.toFixed(3) }
function lineAmount(item) { return calculateLineAmount(item) }
function cartKey(item) { return cartLineKey(item) }
function selectedUnit(item) { return item.unit_options?.[item.unit_index] || item.unit_options?.[0] || { label: '基本单位', multiplier: 1 } }
function storageGet(key) { try { return uni.getStorageSync(key) || null } catch (error) { return null } }
function storageSet(key, value) { try { uni.setStorageSync(key, value) } catch (error) { console.warn('保存本地 POS 状态失败', error) } }
function storageRemove(key) { try { uni.removeStorageSync(key) } catch (error) {} }
function draftKey() { return draftStorageKey(auth.user || {}) }
function pendingKey() { return pendingStorageKey(auth.user || {}) }

function back() { uni.navigateBack({ fail: () => uni.switchTab({ url: '/pages/index/index' }) }) }
function openHistory() { if (canViewHistory.value) uni.navigateTo({ url: '/pages/pos/history' }); else uni.showToast({ title: '没有查看销售记录的权限', icon: 'none' }) }
function setFeedback(text, tone = 'success') { scanFeedback.value = text; feedbackTone.value = tone }
function clearSearch() { productKeyword.value = ''; products.value = []; productSearched.value = false }

async function searchProducts({ reset = true } = {}) {
  if (productLoading.value) return
  const keyword = productKeyword.value.trim()
  if (!keyword) { clearSearch(); return }
  productLoading.value = true
  const nextPage = reset ? 1 : productPage.value + 1
  try {
    const result = await api.posProducts({ search: keyword, page: nextPage, page_size: 20 })
    const rows = normalizeRows(result)
    products.value = reset ? rows : [...products.value, ...rows]
    productPage.value = nextPage
    productSearched.value = true
    hasMoreProducts.value = Boolean(result?.next)
  } catch (error) {
    uni.showToast({ title: error?.message || '商品查询失败', icon: 'none' })
  } finally {
    productLoading.value = false
  }
}

function addProduct(product, barcode = '') {
  const result = addProductToCart(cartItems.value, product, barcode)
  if (result.error) { setFeedback(result.error, 'error'); uni.showToast({ title: result.error, icon: 'none' }); return false }
  cartItems.value = result.items
  setFeedback(`已加入：${productName(product)}`)
  products.value = []; productSearched.value = false; productKeyword.value = ''
  return true
}

async function handleScan(code) {
  if (pendingCheckout.value) return
  setFeedback(`正在查询：${code}`, 'info')
  try {
    let result = await api.posProducts({ barcode: code, page: 1, page_size: 100 })
    let rows = normalizeRows(result)
    if (!rows.length) rows = normalizeRows(await api.posProducts({ search: code, page: 1, page_size: 100 }))
    if (!rows.length) { setFeedback(`未找到条码：${code}`, 'error'); uni.showToast({ title: '未找到商品，可手工搜索', icon: 'none' }); return }
    if (rows.length > 1) { pendingScanCode.value = code; productChoices.value = rows; return }
    addProduct(rows[0], code)
  } catch (error) {
    setFeedback(error?.message || '扫码查询失败', 'error')
  }
}
const { quickScan, scanning } = useBarcodeScanner({ onScan: handleScan })
function chooseScannedProduct(product) { const code = pendingScanCode.value; productChoices.value = []; pendingScanCode.value = ''; addProduct(product, code) }

function normalizeQuantity(index) {
  const item = cartItems.value[index]
  if (!item) return
  item.qty = Math.max(toNumber(item.qty, 1), 1)
  if (lineBaseQty(item) > availableQty(item)) { item.qty = Math.max(Math.floor(availableQty(item) / toNumber(selectedUnit(item).multiplier, 1)), 1); uni.showToast({ title: '数量已调整为可用库存上限', icon: 'none' }) }
}
function increase(index) { const item = cartItems.value[index]; if (!item) return; const old = item.qty; item.qty = toNumber(item.qty, 0) + 1; if (lineBaseQty(item) > availableQty(item)) { item.qty = old; uni.showToast({ title: '数量超过全仓可用库存', icon: 'none' }) } }
function decrease(index) { const item = cartItems.value[index]; if (!item) return; if (toNumber(item.qty, 1) <= 1) removeItem(index); else item.qty = toNumber(item.qty, 1) - 1 }
function removeItem(index) { if (pendingCheckout.value) return; cartItems.value.splice(index, 1) }
function changeUnit(index, event) {
  const item = cartItems.value[index]; if (!item) return
  const previous = item.unit_index; item.unit_index = Number(event?.detail?.value || 0)
  if (lineBaseQty(item) > availableQty(item)) { item.unit_index = previous; uni.showToast({ title: '该包装数量超过全仓可用库存', icon: 'none' }); return }
  const duplicateIndex = cartItems.value.findIndex((row, rowIndex) => rowIndex !== index && cartLineKey(row) === cartLineKey(item))
  if (duplicateIndex >= 0) { cartItems.value[duplicateIndex].qty = toNumber(cartItems.value[duplicateIndex].qty, 0) + toNumber(item.qty, 0); cartItems.value.splice(index, 1) }
}

function saveDraft() {
  if (!restored.value) return
  if (!cartItems.value.length) { if (!pendingCheckout.value) storageRemove(draftKey()); return }
  storageSet(draftKey(), { saved_at: Date.now(), cart_items: cartItems.value, payment_method: paymentMethod.value, amount_received: amountReceived.value, payment_reference: paymentReference.value, remark: remark.value })
}
function restoreState() {
  const draft = storageGet(draftKey())
  if (draft?.cart_items?.length && Date.now() - Number(draft.saved_at || 0) < 24 * 60 * 60 * 1000) {
    cartItems.value = draft.cart_items.map((item) => ({ ...item, unit_options: unitOptions(item) }))
    paymentMethod.value = draft.payment_method || 'WECHAT'; amountReceived.value = draft.amount_received || ''; paymentReference.value = draft.payment_reference || ''; remark.value = draft.remark || ''
    setFeedback(`已恢复未结账购物车：${cartItems.value.length} 项`, 'info')
  }
  pendingCheckout.value = storageGet(pendingKey())
  restored.value = true
}

async function refreshCartStock() {
  for (const item of cartItems.value) {
    const result = await api.posProducts({ search: item.code || item.sku || item.name, page: 1, page_size: 20 })
    const matched = normalizeRows(result).find((row) => Number(row.id) === Number(item.product_id))
    if (matched) item.available_qty = availableQty(matched)
  }
}

function openPayment() {
  if (!shiftIsOpen.value) { shiftPanelOpen.value = true; uni.showToast({ title: '请先开班', icon: 'none' }); return }
  const error = validateCart(cartItems.value)
  if (error) { uni.showToast({ title: error, icon: 'none' }); return }
  if (paymentMethod.value !== 'CASH') amountReceived.value = totalAmount.value.toFixed(2)
  paymentOpen.value = true
}
function selectPayment(method) { paymentMethod.value = method; amountReceived.value = method === 'CASH' ? '' : totalAmount.value.toFixed(2) }

async function checkout() {
  if (submitting.value || pendingCheckout.value) return
  if (!paymentReady.value) { uni.showToast({ title: '现金实付金额不能小于应收金额', icon: 'none' }); return }
  submitting.value = true
  try {
    await refreshCartStock()
    const error = validateCart(cartItems.value)
    if (error) { uni.showToast({ title: error, icon: 'none' }); return }
    const snapshot = finalizeCheckoutSnapshot(buildCheckoutSnapshot({ items: cartItems.value, paymentMethod: paymentMethod.value, amountReceived: amountReceived.value, referenceNo: paymentReference.value, remark: remark.value }))
    pendingCheckout.value = snapshot
    storageSet(pendingKey(), snapshot)
    await submitPendingSnapshot()
  } catch (error) {
    uni.showToast({ title: error?.message || '结账准备失败', icon: 'none' })
  } finally {
    submitting.value = false
  }
}

async function submitPendingSnapshot() {
  const snapshot = pendingCheckout.value
  if (!snapshot?.payload) return
  try {
    const response = await api.posCheckout(snapshot.payload)
    storageRemove(pendingKey()); storageRemove(draftKey()); pendingCheckout.value = null
    cartItems.value = []; remark.value = ''; paymentReference.value = ''; amountReceived.value = ''; paymentMethod.value = 'WECHAT'; paymentOpen.value = false
    const saleNo = response?.sale?.sale_no || response?.receipt?.sale_no || response?.orders?.[0]?.order_no || ''
    uni.showModal({ title: '销售已记录', content: saleNo ? `单号：${saleNo}` : '库存和销售记录已更新。', showCancel: false })
    await loadShift()
  } catch (error) {
    paymentOpen.value = false
    const status = Number(error?.statusCode || 0)
    if (status === 400 || status === 403) { storageRemove(pendingKey()); pendingCheckout.value = null; uni.showModal({ title: '销售未提交', content: error?.message || '请修改后重试', showCancel: false }) }
    else if (status === 409) { const wait = error?.header?.['Retry-After'] || error?.header?.['retry-after']; uni.showModal({ title: '库存正在被占用', content: wait ? `请等待 ${wait} 秒后点击“重试确认”。` : '请稍后点击“重试确认”，应用会使用原请求避免重复销售。', showCancel: false }) }
    else uni.showModal({ title: '销售结果待确认', content: '网络响应中断，购物车已锁定。恢复网络后点击“重试确认”。', showCancel: false })
    throw error
  }
}
async function retryCheckout() { if (!pendingCheckout.value || submitting.value) return; submitting.value = true; try { await submitPendingSnapshot() } catch (error) {} finally { submitting.value = false } }

async function loadShift() { if (shiftLoading.value) return; shiftLoading.value = true; try { currentShift.value = (await api.posShiftCurrent())?.shift || null; actualCash.value = currentShift.value?.summary?.expected_cash_amount || currentShift.value?.actual_cash_amount || '' } catch (error) { currentShift.value = null } finally { shiftLoading.value = false } }
async function openShift() { if (shiftLoading.value) return; shiftLoading.value = true; try { currentShift.value = (await api.posShiftOpen({ opening_cash_amount: toNumber(openingCash.value, 0).toFixed(2) }))?.shift || null; shiftPanelOpen.value = false; uni.showToast({ title: '开班成功', icon: 'success' }) } catch (error) { uni.showToast({ title: error?.message || '开班失败', icon: 'none' }) } finally { shiftLoading.value = false } }
async function closeShift() {
  if (!currentShift.value?.id || shiftLoading.value) return
  if (cartItems.value.length) { uni.showToast({ title: '请先完成或清空购物车再交班', icon: 'none' }); return }
  const confirmed = await new Promise((resolve) => uni.showModal({ title: '确认交班', content: `现金实点 ${money(actualCash.value)}`, confirmText: '交班', success: (res) => resolve(res.confirm), fail: () => resolve(false) }))
  if (!confirmed) return
  shiftLoading.value = true
  try {
    const payments = (currentShift.value.summary?.payments || []).filter((row) => row.method && !['CASH', 'CREDIT'].includes(row.method)).map((row) => ({ method: row.method, actual_amount: toNumber(row.expected_amount, 0).toFixed(2) }))
    await api.posShiftClose(currentShift.value.id, { actual_cash_amount: toNumber(actualCash.value, 0).toFixed(2), payments, remark: '' })
    currentShift.value = null; shiftPanelOpen.value = false; uni.showToast({ title: '交班完成', icon: 'success' })
  } catch (error) { uni.showToast({ title: error?.message || '交班失败', icon: 'none' }) } finally { shiftLoading.value = false }
}

watch([cartItems, paymentMethod, amountReceived, paymentReference, remark], saveDraft, { deep: true })
onShow(async () => {
  await auth.loadProfile().catch(() => null)
  if (!restored.value) restoreState()
  await loadShift()
})
</script>

<style scoped>
.pos-page { height: 100vh; overflow: hidden; background: #fff; color: #0f172a; }.pos-header { height: calc(104rpx + var(--status-bar-height, 0px)); padding: var(--status-bar-height, 0px) 28rpx 0; display: flex; align-items: center; justify-content: space-between; border-bottom: 1rpx solid #eef2f6; background: #fff; }.header-title { font-size: 36rpx; font-weight: 750; }.header-button { width: 72rpx; height: 72rpx; display: flex; align-items: center; justify-content: center; color: #0f172a; font-size: 48rpx; }.header-button :deep(svg){font-size:34rpx}.header-button.disabled{opacity:.3}
.pos-scroll { height: calc(100vh - 104rpx - var(--status-bar-height, 0px) - 152rpx - env(safe-area-inset-bottom)); }.pos-content { padding: 22rpx 30rpx 0; }.status-row { display:flex;align-items:center;justify-content:space-between;gap:16rpx;margin-bottom:22rpx}.shift-chip{min-height:52rpx;padding:0 18rpx;border-radius:14rpx;background:#f1f5f9;color:#64748b;display:flex;align-items:center;gap:12rpx;font-size:23rpx}.shift-chip.open{background:#edf9f3;color:#159357}.shift-dot{width:12rpx;height:12rpx;border-radius:50%;background:currentColor}.stock-scope{color:#64748b;font-size:22rpx}
.pending-banner{margin-bottom:22rpx;padding:22rpx;border:1rpx solid #f5c16c;border-radius:20rpx;background:#fff7ed;display:flex;align-items:center;gap:18rpx}.pending-copy{flex:1;color:#9a5b00;font-size:22rpx}.pending-copy text{display:block}.pending-title{margin-bottom:4rpx;color:#713f12;font-size:26rpx;font-weight:700}.retry-button{min-height:72rpx;padding:0 20rpx;border-radius:14rpx;background:#e77800;color:#fff;font-size:24rpx;display:flex;align-items:center}
.scan-button{width:100%;height:122rpx;border-radius:24rpx;background:#1677ff;color:#fff;display:flex;align-items:center;justify-content:center;gap:24rpx;font-size:36rpx;font-weight:720;box-shadow:0 16rpx 36rpx rgba(22,119,255,.18)}.scan-button :deep(svg){font-size:54rpx}.search-box{height:92rpx;margin-top:22rpx;padding:0 22rpx;border:1rpx solid #cbd5e1;border-radius:18rpx;display:flex;align-items:center;gap:18rpx;color:#64748b;font-size:34rpx}.search-box input{flex:1;height:100%;font-size:27rpx}.search-clear{font-size:23rpx;color:#1677ff}.scan-feedback{margin-top:20rpx;padding:18rpx 22rpx;border-radius:16rpx;font-size:25rpx}.scan-feedback.success{background:#edf9f3;color:#087443}.scan-feedback.error{background:#fff1f2;color:#c8323a}.scan-feedback.info{background:#eff6ff;color:#1456ae}
.search-results{margin-top:22rpx;border:1rpx solid #e2e8f0;border-radius:20rpx;overflow:hidden}.result-heading{height:72rpx;padding:0 20rpx;background:#f8fafc;display:flex;align-items:center;justify-content:space-between;font-size:23rpx;color:#64748b}.product-result{min-height:118rpx;padding:18rpx 16rpx 18rpx 20rpx;border-top:1rpx solid #e2e8f0;display:flex;align-items:center;gap:12rpx}.product-main{flex:1;min-width:0}.product-name,.product-meta{display:block}.product-name{font-size:27rpx;font-weight:650}.product-meta{margin-top:5rpx;color:#64748b;font-size:21rpx}.result-price{font-size:25rpx;font-weight:700}.add-btn{min-height:68rpx;padding:0 18rpx;border:1rpx solid #1677ff;border-radius:14rpx;background:#fff;color:#1677ff;font-size:23rpx;display:flex;align-items:center}.empty-state{padding:36rpx 20rpx;color:#94a3b8;font-size:24rpx;text-align:center}.load-more{width:100%;height:78rpx;background:#fff;color:#1677ff;font-size:24rpx;display:flex;align-items:center;justify-content:center;border-top:1rpx solid #e2e8f0}
.cart-heading{margin:34rpx 2rpx 14rpx;display:flex;align-items:center;justify-content:space-between;font-size:29rpx;font-weight:720}.cart-heading text:last-child{color:#94a3b8;font-size:22rpx;font-weight:450}.cart-empty{min-height:210rpx;border:1rpx dashed #cbd5e1;border-radius:22rpx;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18rpx;color:#94a3b8;font-size:23rpx}.empty-scan{font-size:54rpx;color:#cbd5e1}.cart-card{margin-bottom:20rpx;padding:22rpx;border:1rpx solid #e2e8f0;border-radius:22rpx}.cart-top{display:flex;align-items:flex-start;gap:16rpx}.product-placeholder{width:76rpx;height:76rpx;flex:0 0 auto;border-radius:16rpx;background:#f8fafc;color:#cbd5e1;display:flex;align-items:center;justify-content:center;font-size:38rpx}.cart-copy{flex:1;min-width:0}.cart-name,.cart-code{display:block}.cart-name{font-size:28rpx;font-weight:680}.cart-code{margin-top:5rpx;color:#64748b;font-size:21rpx}.cart-amount{font-size:29rpx;font-weight:750}.cart-controls{margin-top:22rpx;display:flex;align-items:center;gap:12rpx}.quantity-button{width:66rpx;height:66rpx;padding:0;border:1rpx solid #94a3b8;border-radius:50%;background:#fff;color:#475569;font-size:34rpx;display:flex;align-items:center;justify-content:center}.quantity-button.add{border-color:#1677ff;color:#1677ff}.quantity-input{width:92rpx;height:66rpx;border:1rpx solid #cbd5e1;border-radius:12rpx;text-align:center;font-size:27rpx}.unit-picker{margin-left:auto}.unit-value{min-width:116rpx;height:66rpx;padding:0 16rpx;border:1rpx solid #cbd5e1;border-radius:12rpx;display:flex;align-items:center;justify-content:space-between;gap:10rpx;font-size:25rpx}.price-row{margin-top:18rpx;padding-top:16rpx;border-top:1rpx solid #f1f5f9;display:flex;align-items:center;gap:12rpx;color:#64748b;font-size:22rpx}.fixed-price{color:#0f172a;font-size:25rpx}.remove-link{margin-left:auto;color:#e5484d}.remark-row{min-height:90rpx;margin-top:26rpx;padding:0 6rpx;border-bottom:1rpx solid #e2e8f0;display:flex;align-items:center;gap:18rpx;font-size:25rpx}.remark-row input{flex:1;text-align:right;font-size:25rpx}.scroll-spacer{height:40rpx}
.checkout-bar{min-height:152rpx;padding:18rpx 30rpx;position:fixed;left:0;right:0;bottom:0;border-top:1rpx solid #e2e8f0;background:#fff;display:flex;align-items:center;gap:24rpx;box-shadow:0 -10rpx 30rpx rgba(15,23,42,.04)}.checkout-total{flex:1;color:#64748b;font-size:23rpx}.checkout-total text{display:block}.checkout-total view{margin-top:4rpx;color:#0f172a;font-size:28rpx}.checkout-total strong{margin-left:8rpx;color:#1677ff;font-size:36rpx}.checkout-bar>button{width:276rpx;height:92rpx;border-radius:18rpx;background:#0f766e;color:#fff;font-size:31rpx;font-weight:720;display:flex;align-items:center;justify-content:center}
.overlay{position:fixed;z-index:20;inset:0;background:rgba(15,23,42,.72);display:flex;align-items:flex-end}.bottom-sheet{width:100%;max-height:94vh;padding:18rpx 30rpx 24rpx;border-radius:32rpx 32rpx 0 0;background:#fff}.sheet-handle{width:80rpx;height:10rpx;margin:0 auto 26rpx;border-radius:99rpx;background:#d1d5db}.sheet-title{display:block;font-size:38rpx;font-weight:780;text-align:center}.amount-label{display:block;margin-top:24rpx;color:#64748b;font-size:25rpx;text-align:center}.amount-due{display:block;margin-top:4rpx;font-size:64rpx;font-weight:800;text-align:center}.sheet-divider{height:1rpx;margin:26rpx 0;background:#e2e8f0}.form-label{display:block;margin-bottom:14rpx;color:#475569;font-size:25rpx;font-weight:650}.payment-option{height:86rpx;margin-bottom:12rpx;padding:0 20rpx;border:1rpx solid #e2e8f0;border-radius:16rpx;display:flex;align-items:center;gap:18rpx;font-size:28rpx}.payment-option.selected{border:2rpx solid #1677ff}.payment-logo{width:52rpx;height:52rpx;border-radius:14rpx;background:#f1f5f9;color:#475569;display:flex;align-items:center;justify-content:center;font-size:24rpx;font-weight:750}.payment-logo.wechat,.payment-logo.cash{background:#edf9f3;color:#159357}.payment-logo.alipay{background:#eff6ff;color:#1677ff}.payment-option>text{flex:1}.radio{width:40rpx;height:40rpx;border:3rpx solid #9ca3af;border-radius:50%;display:flex;align-items:center;justify-content:center}.payment-option.selected .radio{border-color:#1677ff}.payment-option.selected .radio view{width:22rpx;height:22rpx;border-radius:50%;background:#1677ff}.cash-row,.change-row,.shift-summary{min-height:82rpx;display:flex;align-items:center;justify-content:space-between;border-bottom:1rpx solid #e2e8f0;font-size:25rpx}.cash-row>view{display:flex;align-items:center}.cash-row input{width:180rpx;text-align:right;font-size:30rpx}.change-row strong{color:#0f766e;font-size:30rpx}.reference-input,.sheet-input{width:100%;height:84rpx;margin-top:14rpx;padding:0 20rpx;border:1rpx solid #cbd5e1;border-radius:14rpx;font-size:26rpx}.payment-notice{height:66rpx;margin-top:20rpx;border-radius:12rpx;background:#eff6ff;color:#1456ae;display:flex;align-items:center;justify-content:center;font-size:23rpx}.sheet-actions{margin-top:20rpx;display:flex;gap:14rpx}.sheet-actions button{height:88rpx;border-radius:15rpx;font-size:27rpx;font-weight:680;display:flex;align-items:center;justify-content:center}.cancel-button{width:30%;border:1rpx solid #94a3b8;background:#fff;color:#0f172a}.confirm-button{flex:1;background:#0f766e;color:#fff}.confirm-button.full{width:100%;height:88rpx;margin-top:24rpx;border-radius:16rpx;display:flex;align-items:center;justify-content:center;font-size:29rpx;font-weight:700}.bottom-sheet.compact{padding-bottom:34rpx}.shift-summary strong{font-size:27rpx}.danger-action{width:100%;height:88rpx;margin-top:24rpx;border-radius:16rpx;background:#fff1f2;color:#d4323a;border:1rpx solid #f1aeb2;display:flex;align-items:center;justify-content:center;font-size:29rpx;font-weight:700}.bottom-sheet.choices{max-height:76vh}.choice-hint{display:block;margin:14rpx 0 20rpx;color:#64748b;font-size:23rpx;text-align:center}.choice-list{max-height:50vh}.choice-row{min-height:112rpx;padding:18rpx 4rpx;border-bottom:1rpx solid #e2e8f0;display:flex;align-items:center;justify-content:space-between;gap:20rpx}.choice-row view{flex:1}.choice-row view text{display:block}.choice-row view text:first-child{font-size:27rpx;font-weight:650}.choice-row view text:last-child{margin-top:5rpx;color:#64748b;font-size:22rpx}.choice-row>text{color:#0f766e;font-weight:700}
@media (min-width: 768px){.pos-content,.checkout-bar{max-width:900rpx;margin-left:auto;margin-right:auto}.checkout-bar{left:50%;right:auto;width:900rpx;transform:translateX(-50%)}.bottom-sheet{max-width:760rpx;margin:0 auto}}
</style>

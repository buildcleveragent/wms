<template>
  <view class="history-page page">
    <view v-if="recoveryError" class="pending-return">{{ recoveryError }}</view>
    <view v-if="pendingReturn" class="pending-return">
      <text>{{ pendingReturn.display.sale_no || pendingReturn.payload.sale_id }}：退货结果待确认</text>
      <text>已保留原请求，恢复网络后继续确认，避免重复退货。</text>
      <button :disabled="returnSubmitting || !canReturn" @click="retryReturn">继续确认结果</button>
    </view>
    <view class="search-box">
      <WmsIcon name="search" />
      <input v-model.trim="keyword" placeholder="搜索销售单号或小票号" confirm-type="search" @confirm="loadSales" />
      <button :disabled="loading" @click="loadSales">查询</button>
    </view>

    <view v-if="!sales.length && !loading" class="empty">暂无销售记录</view>
    <view v-for="sale in sales" :key="sale.id" class="sale-record">
      <view class="sale-summary" @click="toggleDetail(sale)">
        <view :class="['sale-status-icon', { voided: sale.status === 'VOIDED' }]">
          <text>{{ sale.status === 'VOIDED' ? '×' : '✓' }}</text>
        </view>
        <view class="sale-copy">
          <view class="sale-title-row"><text>{{ saleNo(sale) }}</text><text :class="{ voided: sale.status === 'VOIDED' }">{{ statusLabel(sale.status) }}</text></view>
          <text class="sale-meta">{{ paymentLabel(sale.payment?.method || sale.receipt?.payment?.method) }} · {{ sale.created_at ? formatTime(sale.created_at) : '' }}</text>
          <text class="sale-meta">{{ saleItemCount(sale) }} 件商品</text>
        </view>
        <view class="sale-money"><text>{{ money(sale.total_amount) }}</text><text :class="['expand-arrow', { open: expandedId === sale.id }]">⌄</text></view>
      </view>

      <view v-if="expandedId === sale.id" class="sale-detail">
        <view v-if="detailLoading" class="detail-state">正在加载详情…</view>
        <template v-else>
          <view v-for="line in detailLines" :key="line.id" class="detail-line">
            <view><text>{{ line.product_name || line.name || line.product_code }}</text><text>{{ ownerLabel(line) }} · {{ line.product_spec || line.product_code || '' }}</text></view>
            <text>× {{ qty(line.qty) }} {{ line.base_unit || '基本单位' }}</text><strong>{{ money(line.amount) }}</strong>
          </view>
          <view class="detail-actions">
            <button class="detail-button" @click="showDetailReceipt">查看详情</button>
            <button v-if="canReturn" class="return-button" :disabled="sale.status === 'VOIDED' || !!recoveryError || !!pendingReturn || returnSubmitting || voidSubmitting" @click="startReturn">退货</button>
            <button v-if="canVoid" class="void-button" :disabled="sale.status === 'VOIDED' || !!recoveryError || !!pendingReturn || returnSubmitting || voidSubmitting" @click="voidSale">作废</button>
          </view>
        </template>
      </view>
    </view>
    <button v-if="hasMore" class="load-more" :disabled="loading" @click="loadMore">{{ loading ? '加载中…' : '加载更多' }}</button>

    <view v-if="returnOpen" class="overlay" @click="closeReturn">
      <view class="return-sheet safe-bottom" @click.stop>
        <view class="sheet-handle" /><text class="sheet-title">销售退货</text>
        <text class="return-sale-no">{{ saleNo(returnSale) }}</text>
        <scroll-view class="return-lines" scroll-y>
          <view v-for="line in returnLines" :key="line.id" class="return-line">
            <view><text>{{ line.product_name || line.product_code }}</text><text>{{ ownerLabel(line) }} · 最多可退 {{ qty(line.returnable_qty) }} {{ line.base_unit || '基本单位' }}</text></view>
            <input v-model="line.return_qty" :disabled="returnSubmitting" type="digit" placeholder="0" @blur="normalizeReturnQty(line)" />
          </view>
        </scroll-view>
        <input v-model.trim="returnReason" :disabled="returnSubmitting" class="sheet-input" placeholder="请输入退货原因" />
        <picker v-if="returnTotal > 0" :disabled="returnSubmitting" mode="selector" :range="paymentMethods.map((item) => item.label)" :value="returnPaymentIndex" @change="changeReturnPayment">
          <view class="refund-picker">退款方式 <strong>{{ paymentLabel(returnPaymentMethod) }}</strong><text>⌄</text></view>
        </picker>
        <view class="refund-total"><text>退款金额</text><strong>{{ money(returnTotal) }}</strong></view>
        <view class="sheet-actions"><button class="cancel" :disabled="returnSubmitting" @click="closeReturn">取消</button><button class="confirm" :disabled="returnSubmitting || !hasReturnQty || !returnReason" @click="submitReturn">确认退货并记录退款</button></view>
      </view>
    </view>
    <view v-if="receiptSale" class="overlay" @click="receiptSale = null">
      <view class="return-sheet receipt-sheet safe-bottom" @click.stop>
        <text class="sheet-title">{{ saleNo(receiptSale) }}</text>
        <view class="receipt-meta">仓库：{{ receiptSale.warehouse_name || receiptSale.warehouse_id || '-' }} · 操作员：{{ receiptSale.cashier_name || receiptSale.receipt?.cashier || receiptSale.cashier_id || '-' }}</view>
        <view class="receipt-meta">客户：{{ receiptSale.receipt?.customer?.name || '散客' }}</view>
        <view class="receipt-meta">{{ formatTime(receiptSale.created_at) }} · {{ statusLabel(receiptSale.status) }}</view>
        <view v-for="line in receiptSale.lines" :key="line.id" class="receipt-line">
          <text>{{ ownerLabel(line) }} · {{ line.product_name }} {{ line.product_spec }}</text>
          <text>{{ qty(line.qty) }} {{ line.base_unit || '基本单位' }} · {{ money(line.amount) }}</text>
        </view>
        <view v-for="order in receiptSale.orders" :key="order.id" class="receipt-meta">{{ order.owner_name || order.owner_id }} · 出库单 {{ order.order_no || order.id }} · {{ order.customer_name || '散客' }} · {{ order.status }}</view>
        <view class="refund-total"><text>合计</text><strong>{{ money(receiptSale.total_amount) }}</strong></view>
        <view class="sheet-actions"><button class="confirm" @click="receiptSale = null">关闭详情</button></view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { onBackPress, onShow, onUnload } from '@dcloudio/uni-app'
import WmsIcon from '@/components/WmsIcon.vue'
import { useAuth } from '@/store/auth'
import { api, createIdempotencyUuid } from '@/utils/request'
import { hasPermission } from '@/utils/mobileAccess'
import { POS_PAYMENT_METHODS, money, normalizeRows, roundMoney, toNumber } from '@/utils/posMobile'
import { businessScope, captureSession, clearPending, definitelyRejected, isCurrentSession, operationKey, readPending, writePending } from '@/utils/pendingOperation'

const auth = useAuth()
const keyword = ref('')
const sales = ref([])
const page = ref(1)
const hasMore = ref(false)
const loading = ref(false)
const expandedId = ref(null)
const activeDetail = ref(null)
const detailLoading = ref(false)
const returnOpen = ref(false)
const returnLines = ref([])
const returnReason = ref('')
const returnPaymentMethod = ref('CASH')
const returnSubmitting = ref(false)
const voidSubmitting = ref(false)
const returnSale = ref(null)
const receiptSale = ref(null)
const pendingReturn = ref(null)
const recoveryError = ref('')
let detailSequence = 0
let listSequence = 0
const paymentMethods = POS_PAYMENT_METHODS

const canVoid = computed(() => hasPermission(auth, 'pos.change_possale'))
const canReturn = computed(() => hasPermission(auth, 'pos.add_posreturn') && hasPermission(auth, 'pos.add_posrefund'))
const detailLines = computed(() => Array.isArray(activeDetail.value?.lines) ? activeDetail.value.lines : [])
const returnPaymentIndex = computed(() => Math.max(0, paymentMethods.findIndex((item) => item.value === returnPaymentMethod.value)))
const returnTotal = computed(() => returnLines.value.reduce((sum, line) => sum + returnLineAmount(line), 0))
const hasReturnQty = computed(() => returnLines.value.some((line) => toNumber(line.return_qty, 0) >= 0.001))
function ownerLabel(line) { return `${line.owner_name || '货主'}（${line.owner_id ?? '-'}）` }
function currentDetail() { return !detailLoading.value && activeDetail.value?.id === expandedId.value ? activeDetail.value : null }
function returnKey() { return operationKey(auth, 'pos-return') }

function saleNo(sale = {}) { return sale.sale_no || sale.src_bill_no || String(sale.id || '-') }
function statusLabel(status) { return status === 'VOIDED' ? '已作废' : status === 'COMPLETED' ? '已完成' : status || '-' }
function paymentLabel(method) { return paymentMethods.find((item) => item.value === method)?.label || method || '其他' }
function saleItemCount(sale = {}) { const lines = Array.isArray(sale.lines) ? sale.lines : []; return lines.reduce((sum, line) => sum + toNumber(line.qty, 0), sale.item_count || sale.total_qty || 0) }
function qty(value) { const n = toNumber(value, 0); return Number.isInteger(n) ? String(n) : n.toFixed(3) }
function formatTime(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')} ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}` }

async function loadSales({ append = false } = {}) {
  if (!businessScope(auth) || (loading.value && append)) return
  const sequence = ++listSequence
  const session = captureSession(auth)
  if (!append) { detailSequence++; expandedId.value = null; activeDetail.value = null; detailLoading.value = false }
  loading.value = true
  const targetPage = append ? page.value + 1 : 1
  try {
    const result = await api.posSales({ search: keyword.value, page: targetPage, page_size: 20 })
    if (sequence !== listSequence || !isCurrentSession(auth, session)) return
    const rows = normalizeRows(result)
    sales.value = append ? [...sales.value, ...rows] : rows
    page.value = targetPage
    hasMore.value = Boolean(result?.next)
  } catch (error) { if (sequence === listSequence && isCurrentSession(auth, session)) uni.showToast({ title: error?.message || '销售记录加载失败', icon: 'none' }) }
  finally { if (sequence === listSequence && isCurrentSession(auth, session)) loading.value = false }
}
function loadMore() { loadSales({ append: true }) }

async function toggleDetail(sale) {
  const sequence = ++detailSequence
  const session = captureSession(auth)
  activeDetail.value = null
  if (expandedId.value === sale.id) { expandedId.value = null; detailLoading.value = false; return }
  expandedId.value = sale.id; detailLoading.value = true
  const current = () => sequence === detailSequence && expandedId.value === sale.id && isCurrentSession(auth, session)
  try { const result = await api.posSaleDetail(sale.id); if (current() && result?.id === sale.id) activeDetail.value = result }
  catch (error) { if (current()) { activeDetail.value = null; uni.showToast({ title: error?.message || '销售详情加载失败', icon: 'none' }) } }
  finally { if (current()) detailLoading.value = false }
}
function showDetailReceipt() {
  const sale = currentDetail()
  if (sale) receiptSale.value = JSON.parse(JSON.stringify(sale))
}

function returnLineAmount(line) {
  const originalQty = toNumber(line.qty, 0)
  const originalAmount = toNumber(line.amount, 0)
  const returnedQty = toNumber(line.returned_qty, 0)
  const returnedAmount = toNumber(line.returned_amount, 0)
  const currentQty = Math.min(toNumber(line.return_qty, 0), toNumber(line.returnable_qty ?? line.qty, 0))
  if (originalQty <= 0 || currentQty <= 0) return 0
  const cumulativeQty = Math.min(returnedQty + currentQty, originalQty)
  const cumulativeAmount = Math.abs(cumulativeQty - originalQty) < 0.0005 ? originalAmount : roundMoney(originalAmount * cumulativeQty / originalQty)
  return Math.max(roundMoney(cumulativeAmount - returnedAmount), 0)
}
function startReturn() {
  const sale = currentDetail()
  if (!sale || !canReturn.value || recoveryError.value || pendingReturn.value || returnSubmitting.value || voidSubmitting.value) return
  const rows = detailLines.value.filter((line) => toNumber(line.returnable_qty ?? line.qty, 0) > 0)
  if (!rows.length) { uni.showToast({ title: '该销售单已无可退数量', icon: 'none' }); return }
  returnLines.value = rows.map((line) => ({ ...line, returnable_qty: toNumber(line.returnable_qty ?? line.qty, 0), return_qty: '' }))
  returnSale.value = JSON.parse(JSON.stringify(sale))
  const originalMethod = sale.payment?.method
  returnPaymentMethod.value = paymentMethods.some((item) => item.value === originalMethod) ? originalMethod : 'CASH'
  returnReason.value = ''; returnOpen.value = true
}
function closeReturn() { if (returnSubmitting.value) return; returnOpen.value = false; returnSale.value = null; returnLines.value = []; returnReason.value = '' }
function normalizeReturnQty(line) { line.return_qty = Math.min(Math.max(toNumber(line.return_qty, 0), 0), toNumber(line.returnable_qty, 0)) || '' }
function changeReturnPayment(event) { returnPaymentMethod.value = paymentMethods[Number(event?.detail?.value || 0)]?.value || 'CASH' }
async function submitReturn() {
  if (recoveryError.value || !canReturn.value || !returnSale.value?.id || pendingReturn.value || returnSubmitting.value) return
  const lines = returnLines.value.map((line) => ({ sale_line_id: line.id, qty: toNumber(line.return_qty, 0) })).filter((line) => line.qty > 0)
  if (!lines.length || !returnReason.value || returnSubmitting.value) return
  const session = captureSession(auth)
  const amount = returnTotal.value
  const display = { sale_no: saleNo(returnSale.value) }
  const payload = { sale_id: returnSale.value.id, reason: returnReason.value.trim(), idempotency_key: createIdempotencyUuid(), lines: lines.map((line) => ({ ...line, qty: line.qty.toFixed(3) })), refunds: amount > 0 ? [{ method: returnPaymentMethod.value, amount: amount.toFixed(2), reference_no: '' }] : [] }
  returnSubmitting.value = true
  try {
    const confirmed = await new Promise((resolve) => uni.showModal({ title: '确认退货', content: `${display.sale_no}：退货并记录退款 ${money(amount)}？`, confirmText: '确认退货', success: (res) => resolve(res.confirm), fail: () => resolve(false) }))
    if (!confirmed || !isCurrentSession(auth, session)) return
    pendingReturn.value = writePending(returnKey(), payload, display)
    await sendPendingReturn(session)
  } catch (error) { if (isCurrentSession(auth, session)) uni.showToast({ title: error?.message || '退货失败', icon: 'none' }) }
  finally { if (isCurrentSession(auth, session)) returnSubmitting.value = false }
}

async function sendPendingReturn(session) {
  const pending = pendingReturn.value
  const key = returnKey()
  if (!pending || !isCurrentSession(auth, session)) return
  try {
    const response = await api.posReturnCreate(pending.payload)
    if (!response?.return?.id) throw new Error('退货响应不完整，请继续确认原请求')
    if (!isCurrentSession(auth, session)) return
    clearPending(key); pendingReturn.value = null
    returnOpen.value = false; returnLines.value = []; returnReason.value = ''; returnSale.value = null
    uni.showToast({ title: '退货完成', icon: 'success' })
    await loadSales()
  } catch (error) {
    if (!isCurrentSession(auth, session)) return
    if (definitelyRejected(error)) { clearPending(key); pendingReturn.value = null }
    else { returnOpen.value = false; uni.showToast({ title: '退货结果待确认，请继续确认原请求', icon: 'none' }) }
    throw error
  }
}

async function retryReturn() {
  if (!pendingReturn.value || returnSubmitting.value || !canReturn.value) return
  const session = captureSession(auth)
  returnSubmitting.value = true
  try { await sendPendingReturn(session) }
  catch (error) { if (isCurrentSession(auth, session)) uni.showToast({ title: error?.message || '退货结果仍待确认', icon: 'none' }) }
  finally { if (isCurrentSession(auth, session)) returnSubmitting.value = false }
}

async function voidSale() {
  const sale = currentDetail()
  if (!sale || !canVoid.value || recoveryError.value || voidSubmitting.value || returnSubmitting.value || pendingReturn.value) return
  const session = captureSession(auth)
  voidSubmitting.value = true
  try {
    const reasonResult = await new Promise((resolve) => uni.showModal({ title: `作废 ${saleNo(sale)}`, content: '库存将按原扣减明细恢复，请输入作废原因。', editable: true, placeholderText: '作废原因', confirmText: '确认作废', success: resolve, fail: () => resolve({ confirm: false }) }))
    const reason = String(reasonResult.content || '').trim()
    if (!reasonResult.confirm || !reason || !isCurrentSession(auth, session)) return
    await api.posSaleVoid(sale.id, { reason })
    if (!isCurrentSession(auth, session)) return
    uni.showToast({ title: '已作废并恢复库存', icon: 'success' }); await loadSales()
  } catch (error) { if (isCurrentSession(auth, session)) uni.showToast({ title: error?.message || '作废失败', icon: 'none' }) }
  finally { if (isCurrentSession(auth, session)) voidSubmitting.value = false }
}

function restorePendingReturn() {
  try { pendingReturn.value = readPending(returnKey()); recoveryError.value = ''; return true }
  catch (error) { recoveryError.value = error.message; uni.showToast({ title: error.message, icon: 'none' }); return false }
}
watch(() => businessScope(auth), (scope) => {
  detailSequence++; listSequence++; sales.value = []; expandedId.value = null; activeDetail.value = null
  loading.value = false; detailLoading.value = false; pendingReturn.value = null
  returnSubmitting.value = false; voidSubmitting.value = false; receiptSale.value = null; closeReturn()
  // A profile refresh can finish after onShow (for example when leaving the workbench).
  if (scope && restorePendingReturn()) void loadSales()
}, { flush: 'sync' })
onBackPress(() => { if (returnOpen.value) { closeReturn(); return true }; if (receiptSale.value) { receiptSale.value = null; return true }; return false })
onUnload(() => { detailSequence++; listSequence++ })
onShow(async () => {
  await auth.loadProfile().catch(() => null)
  if (!businessScope(auth)) return
  if (restorePendingReturn() && !loading.value) await loadSales()
})
</script>

<style scoped>
.history-page{padding-top:24rpx}.search-box{height:88rpx;padding:0 20rpx;border-radius:18rpx;background:#f8fafc;display:flex;align-items:center;gap:16rpx;color:#475569;font-size:34rpx}.search-box input{flex:1;height:100%;font-size:26rpx}.search-box button{min-height:66rpx;padding:0 18rpx;border-radius:12rpx;background:#0f766e;color:#fff;font-size:23rpx;display:flex;align-items:center}.empty{padding:100rpx 0;color:#94a3b8;text-align:center}.sale-record{border-bottom:1rpx solid #e2e8f0}.sale-summary{min-height:188rpx;padding:28rpx 4rpx;display:flex;align-items:flex-start;gap:18rpx}.sale-status-icon{width:54rpx;height:54rpx;flex:0 0 auto;border-radius:50%;background:#0f8b88;color:#fff;display:flex;align-items:center;justify-content:center;font-size:34rpx}.sale-status-icon.voided{background:#e5484d}.sale-copy{flex:1;min-width:0}.sale-title-row{display:flex;align-items:center;justify-content:space-between;gap:12rpx}.sale-title-row text:first-child{font-size:29rpx;font-weight:750}.sale-title-row text:last-child{color:#0f766e;font-size:23rpx}.sale-title-row text.voided{color:#e5484d}.sale-meta{display:block;margin-top:10rpx;color:#64748b;font-size:23rpx}.sale-money{width:130rpx;text-align:right}.sale-money>text:first-child{display:block;font-size:31rpx;font-weight:750}.expand-arrow{display:block;margin-top:18rpx;color:#0f766e;font-size:26rpx;transition:transform .2s}.expand-arrow.open{transform:rotate(180deg)}
.sale-detail{margin:0 -28rpx;padding:20rpx 32rpx 28rpx;background:#f5fbfa;border-top:1rpx solid #d7efeb}.detail-state{padding:28rpx;color:#64748b;text-align:center}.detail-line{min-height:84rpx;display:flex;align-items:center;gap:14rpx;border-bottom:1rpx solid #dcebe9;font-size:23rpx}.detail-line view{flex:1;min-width:0}.detail-line view text{display:block}.detail-line view text:first-child{font-size:25rpx;font-weight:620}.detail-line view text:last-child{color:#64748b;font-size:21rpx}.detail-line strong{width:120rpx;text-align:right}.detail-actions{display:flex;gap:14rpx;margin-top:24rpx}.detail-actions button{height:80rpx;border-radius:14rpx;background:#fff;display:flex;align-items:center;justify-content:center;font-size:25rpx;font-weight:650}.detail-button{flex:1;border:1rpx solid #0f766e;color:#0f766e}.return-button{width:150rpx;border:1rpx solid #e77800;color:#e77800}.void-button{width:150rpx;border:1rpx solid #e5484d;color:#e5484d}.load-more{width:100%;height:86rpx;margin-top:20rpx;border:1rpx solid #cbd5e1;border-radius:16rpx;background:#fff;color:#0f766e;display:flex;align-items:center;justify-content:center;font-size:25rpx}
.overlay{position:fixed;z-index:20;inset:0;background:rgba(15,23,42,.72);display:flex;align-items:flex-end}.return-sheet{width:100%;max-height:92vh;padding:18rpx 30rpx 28rpx;border-radius:32rpx 32rpx 0 0;background:#fff}.sheet-handle{width:80rpx;height:10rpx;margin:0 auto 24rpx;border-radius:99rpx;background:#d1d5db}.sheet-title{display:block;font-size:36rpx;font-weight:780;text-align:center}.return-sale-no{display:block;margin-top:5rpx;color:#64748b;font-size:23rpx;text-align:center}.return-lines{max-height:38vh;margin-top:24rpx;border-top:1rpx solid #e2e8f0}.return-line{min-height:100rpx;display:flex;align-items:center;gap:18rpx;border-bottom:1rpx solid #e2e8f0}.return-line view{flex:1}.return-line view text{display:block}.return-line view text:first-child{font-size:25rpx;font-weight:650}.return-line view text:last-child{color:#64748b;font-size:21rpx}.return-line input{width:120rpx;height:66rpx;border:1rpx solid #cbd5e1;border-radius:12rpx;text-align:center}.sheet-input{width:100%;height:82rpx;margin-top:18rpx;padding:0 20rpx;border:1rpx solid #cbd5e1;border-radius:14rpx;font-size:25rpx}.refund-picker,.refund-total{min-height:82rpx;margin-top:12rpx;display:flex;align-items:center;border-bottom:1rpx solid #e2e8f0;font-size:25rpx}.refund-picker strong{margin-left:auto;margin-right:14rpx}.refund-total{justify-content:space-between}.refund-total strong{color:#e77800;font-size:31rpx}.sheet-actions{display:flex;gap:14rpx;margin-top:22rpx}.sheet-actions button{height:88rpx;border-radius:15rpx;display:flex;align-items:center;justify-content:center;font-size:26rpx;font-weight:680}.cancel{width:28%;border:1rpx solid #94a3b8;background:#fff}.confirm{flex:1;background:#0f766e;color:#fff}

.pending-return{margin:12px 0;padding:14px;background:#fff7ed;border:1px solid #f5c16c;border-radius:12px;font-size:14px;overflow-wrap:anywhere}.pending-return text{display:block;margin-bottom:8px}.pending-return button{min-height:48px;background:#e77800;color:white}
.return-sheet{box-sizing:border-box;overflow-y:auto;padding-bottom:max(12px,env(safe-area-inset-bottom))}.return-lines{max-height:34vh}.return-line view{min-width:0}.return-line text,.detail-line text,.receipt-meta,.receipt-line,.sale-title-row{overflow-wrap:anywhere}
.sheet-actions{position:sticky;bottom:0;background:white;padding:10px 0;z-index:1}.sheet-actions button,.detail-actions button,.search-box button,.return-line input,.sheet-input,.refund-picker,.load-more{min-height:48px;min-width:48px}.detail-actions{flex-wrap:wrap}.return-button,.void-button{flex:1}.detail-button{min-width:100%}
.receipt-meta,.receipt-line{font-size:14px;padding:10px 0;border-bottom:1px solid #e2e8f0}.receipt-line text{display:block}.detail-line{flex-wrap:wrap}.detail-line view{min-width:45%}
</style>

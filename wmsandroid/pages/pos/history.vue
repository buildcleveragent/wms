<template>
  <view class="history-page page">
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
            <view><text>{{ line.product_name || line.name || line.product_code }}</text><text>{{ line.product_code || '' }}</text></view>
            <text>× {{ qty(line.qty) }}</text><strong>{{ money(line.amount) }}</strong>
          </view>
          <view class="detail-actions">
            <button class="detail-button" @click="showDetailReceipt">查看详情</button>
            <button v-if="canReturn" class="return-button" :disabled="sale.status === 'VOIDED'" @click="startReturn">退货</button>
            <button v-if="canVoid" class="void-button" :disabled="sale.status === 'VOIDED'" @click="voidSale">作废</button>
          </view>
        </template>
      </view>
    </view>
    <button v-if="hasMore" class="load-more" :disabled="loading" @click="loadMore">{{ loading ? '加载中…' : '加载更多' }}</button>

    <view v-if="returnOpen" class="overlay" @click.self="closeReturn">
      <view class="return-sheet safe-bottom">
        <view class="sheet-handle" /><text class="sheet-title">销售退货</text>
        <text class="return-sale-no">{{ saleNo(activeDetail) }}</text>
        <scroll-view class="return-lines" scroll-y>
          <view v-for="line in returnLines" :key="line.id" class="return-line">
            <view><text>{{ line.product_name || line.product_code }}</text><text>最多可退 {{ qty(line.returnable_qty) }}</text></view>
            <input v-model="line.return_qty" type="digit" placeholder="0" @blur="normalizeReturnQty(line)" />
          </view>
        </scroll-view>
        <input v-model.trim="returnReason" class="sheet-input" placeholder="请输入退货原因" />
        <picker mode="selector" :range="paymentMethods.map((item) => item.label)" :value="returnPaymentIndex" @change="changeReturnPayment">
          <view class="refund-picker">退款方式 <strong>{{ paymentLabel(returnPaymentMethod) }}</strong><text>⌄</text></view>
        </picker>
        <view class="refund-total"><text>退款金额</text><strong>{{ money(returnTotal) }}</strong></view>
        <view class="sheet-actions"><button class="cancel" @click="closeReturn">取消</button><button class="confirm" :disabled="returnSubmitting || returnTotal <= 0 || !returnReason" @click="submitReturn">确认退货并记录退款</button></view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import WmsIcon from '@/components/WmsIcon.vue'
import { useAuth } from '@/store/auth'
import { api, createIdempotencyUuid } from '@/utils/request'
import { hasPermission } from '@/utils/mobileAccess'
import { POS_PAYMENT_METHODS, money, normalizeRows, roundMoney, toNumber } from '@/utils/posMobile'

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
const paymentMethods = POS_PAYMENT_METHODS

const canVoid = computed(() => hasPermission(auth, 'pos.change_possale'))
const canReturn = computed(() => hasPermission(auth, 'pos.add_posreturn') && hasPermission(auth, 'pos.add_posrefund'))
const detailLines = computed(() => Array.isArray(activeDetail.value?.lines) ? activeDetail.value.lines : [])
const returnPaymentIndex = computed(() => Math.max(0, paymentMethods.findIndex((item) => item.value === returnPaymentMethod.value)))
const returnTotal = computed(() => returnLines.value.reduce((sum, line) => sum + returnLineAmount(line), 0))

function saleNo(sale = {}) { return sale.sale_no || sale.src_bill_no || String(sale.id || '-') }
function statusLabel(status) { return status === 'VOIDED' ? '已作废' : status === 'COMPLETED' ? '已完成' : status || '-' }
function paymentLabel(method) { return paymentMethods.find((item) => item.value === method)?.label || method || '其他' }
function saleItemCount(sale = {}) { const lines = Array.isArray(sale.lines) ? sale.lines : []; return lines.reduce((sum, line) => sum + toNumber(line.qty, 0), sale.item_count || sale.total_qty || 0) }
function qty(value) { const n = toNumber(value, 0); return Number.isInteger(n) ? String(n) : n.toFixed(3) }
function formatTime(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')} ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}` }

async function loadSales({ append = false } = {}) {
  if (loading.value) return
  loading.value = true
  const targetPage = append ? page.value + 1 : 1
  try {
    const result = await api.posSales({ search: keyword.value, page: targetPage, page_size: 20 })
    const rows = normalizeRows(result)
    sales.value = append ? [...sales.value, ...rows] : rows
    page.value = targetPage
    hasMore.value = Boolean(result?.next)
    if (!append) { expandedId.value = null; activeDetail.value = null }
  } catch (error) { uni.showToast({ title: error?.message || '销售记录加载失败', icon: 'none' }) }
  finally { loading.value = false }
}
function loadMore() { loadSales({ append: true }) }

async function toggleDetail(sale) {
  if (expandedId.value === sale.id) { expandedId.value = null; activeDetail.value = null; return }
  expandedId.value = sale.id; detailLoading.value = true
  try { activeDetail.value = await api.posSaleDetail(sale.id) }
  catch (error) { expandedId.value = null; uni.showToast({ title: error?.message || '销售详情加载失败', icon: 'none' }) }
  finally { detailLoading.value = false }
}
function showDetailReceipt() {
  const sale = activeDetail.value || {}
  const lineText = detailLines.value.slice(0, 6).map((line) => `${line.product_name || line.product_code} × ${qty(line.qty)}`).join('\n')
  uni.showModal({ title: saleNo(sale), content: `${lineText || '无商品明细'}\n合计 ${money(sale.total_amount)}`, showCancel: false })
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
  const rows = detailLines.value.filter((line) => toNumber(line.returnable_qty ?? line.qty, 0) > 0)
  if (!rows.length) { uni.showToast({ title: '该销售单已无可退数量', icon: 'none' }); return }
  returnLines.value = rows.map((line) => ({ ...line, returnable_qty: toNumber(line.returnable_qty ?? line.qty, 0), return_qty: '' }))
  const originalMethod = activeDetail.value?.payment?.method
  returnPaymentMethod.value = paymentMethods.some((item) => item.value === originalMethod) ? originalMethod : 'CASH'
  returnReason.value = ''; returnOpen.value = true
}
function closeReturn() { if (returnSubmitting.value) return; returnOpen.value = false; returnLines.value = []; returnReason.value = '' }
function normalizeReturnQty(line) { line.return_qty = Math.min(Math.max(toNumber(line.return_qty, 0), 0), toNumber(line.returnable_qty, 0)) || '' }
function changeReturnPayment(event) { returnPaymentMethod.value = paymentMethods[Number(event?.detail?.value || 0)]?.value || 'CASH' }
async function submitReturn() {
  const lines = returnLines.value.map((line) => ({ sale_line_id: line.id, qty: toNumber(line.return_qty, 0) })).filter((line) => line.qty > 0)
  if (!lines.length || !returnReason.value || returnSubmitting.value) return
  const confirmed = await new Promise((resolve) => uni.showModal({ title: '确认退货', content: `退货并记录退款 ${money(returnTotal.value)}？`, confirmText: '确认退货', success: (res) => resolve(res.confirm), fail: () => resolve(false) }))
  if (!confirmed) return
  returnSubmitting.value = true
  try {
    await api.posReturnCreate({
      sale_id: activeDetail.value.id, reason: returnReason.value,
      idempotency_key: createIdempotencyUuid(),
      lines: lines.map((line) => ({ ...line, qty: line.qty.toFixed(3) })),
      refunds: [{ method: returnPaymentMethod.value, amount: returnTotal.value.toFixed(2), reference_no: '' }],
    })
    returnOpen.value = false
    returnLines.value = []
    returnReason.value = ''
    uni.showToast({ title: '退货完成', icon: 'success' })
    await loadSales()
  } catch (error) { uni.showToast({ title: error?.message || '退货失败', icon: 'none' }) }
  finally { returnSubmitting.value = false }
}

async function voidSale() {
  if (!activeDetail.value?.id) return
  const reasonResult = await new Promise((resolve) => uni.showModal({ title: `作废 ${saleNo(activeDetail.value)}`, content: '库存将按原扣减明细恢复，请输入作废原因。', editable: true, placeholderText: '作废原因', confirmText: '确认作废', success: resolve, fail: () => resolve({ confirm: false }) }))
  const reason = String(reasonResult.content || '').trim()
  if (!reasonResult.confirm || !reason) { if (reasonResult.confirm) uni.showToast({ title: '必须填写作废原因', icon: 'none' }); return }
  try { await api.posSaleVoid(activeDetail.value.id, { reason }); uni.showToast({ title: '已作废并恢复库存', icon: 'success' }); await loadSales() }
  catch (error) { uni.showToast({ title: error?.message || '作废失败', icon: 'none' }) }
}

onShow(async () => { await auth.loadProfile().catch(() => null); await loadSales() })
</script>

<style scoped>
.history-page{padding-top:24rpx}.search-box{height:88rpx;padding:0 20rpx;border-radius:18rpx;background:#f8fafc;display:flex;align-items:center;gap:16rpx;color:#475569;font-size:34rpx}.search-box input{flex:1;height:100%;font-size:26rpx}.search-box button{min-height:66rpx;padding:0 18rpx;border-radius:12rpx;background:#0f766e;color:#fff;font-size:23rpx;display:flex;align-items:center}.empty{padding:100rpx 0;color:#94a3b8;text-align:center}.sale-record{border-bottom:1rpx solid #e2e8f0}.sale-summary{min-height:188rpx;padding:28rpx 4rpx;display:flex;align-items:flex-start;gap:18rpx}.sale-status-icon{width:54rpx;height:54rpx;flex:0 0 auto;border-radius:50%;background:#0f8b88;color:#fff;display:flex;align-items:center;justify-content:center;font-size:34rpx}.sale-status-icon.voided{background:#e5484d}.sale-copy{flex:1;min-width:0}.sale-title-row{display:flex;align-items:center;justify-content:space-between;gap:12rpx}.sale-title-row text:first-child{font-size:29rpx;font-weight:750}.sale-title-row text:last-child{color:#0f766e;font-size:23rpx}.sale-title-row text.voided{color:#e5484d}.sale-meta{display:block;margin-top:10rpx;color:#64748b;font-size:23rpx}.sale-money{width:130rpx;text-align:right}.sale-money>text:first-child{display:block;font-size:31rpx;font-weight:750}.expand-arrow{display:block;margin-top:18rpx;color:#0f766e;font-size:26rpx;transition:transform .2s}.expand-arrow.open{transform:rotate(180deg)}
.sale-detail{margin:0 -28rpx;padding:20rpx 32rpx 28rpx;background:#f5fbfa;border-top:1rpx solid #d7efeb}.detail-state{padding:28rpx;color:#64748b;text-align:center}.detail-line{min-height:84rpx;display:flex;align-items:center;gap:14rpx;border-bottom:1rpx solid #dcebe9;font-size:23rpx}.detail-line view{flex:1;min-width:0}.detail-line view text{display:block}.detail-line view text:first-child{font-size:25rpx;font-weight:620}.detail-line view text:last-child{color:#64748b;font-size:21rpx}.detail-line strong{width:120rpx;text-align:right}.detail-actions{display:flex;gap:14rpx;margin-top:24rpx}.detail-actions button{height:80rpx;border-radius:14rpx;background:#fff;display:flex;align-items:center;justify-content:center;font-size:25rpx;font-weight:650}.detail-button{flex:1;border:1rpx solid #0f766e;color:#0f766e}.return-button{width:150rpx;border:1rpx solid #e77800;color:#e77800}.void-button{width:150rpx;border:1rpx solid #e5484d;color:#e5484d}.load-more{width:100%;height:86rpx;margin-top:20rpx;border:1rpx solid #cbd5e1;border-radius:16rpx;background:#fff;color:#0f766e;display:flex;align-items:center;justify-content:center;font-size:25rpx}
.overlay{position:fixed;z-index:20;inset:0;background:rgba(15,23,42,.72);display:flex;align-items:flex-end}.return-sheet{width:100%;max-height:92vh;padding:18rpx 30rpx 28rpx;border-radius:32rpx 32rpx 0 0;background:#fff}.sheet-handle{width:80rpx;height:10rpx;margin:0 auto 24rpx;border-radius:99rpx;background:#d1d5db}.sheet-title{display:block;font-size:36rpx;font-weight:780;text-align:center}.return-sale-no{display:block;margin-top:5rpx;color:#64748b;font-size:23rpx;text-align:center}.return-lines{max-height:38vh;margin-top:24rpx;border-top:1rpx solid #e2e8f0}.return-line{min-height:100rpx;display:flex;align-items:center;gap:18rpx;border-bottom:1rpx solid #e2e8f0}.return-line view{flex:1}.return-line view text{display:block}.return-line view text:first-child{font-size:25rpx;font-weight:650}.return-line view text:last-child{color:#64748b;font-size:21rpx}.return-line input{width:120rpx;height:66rpx;border:1rpx solid #cbd5e1;border-radius:12rpx;text-align:center}.sheet-input{width:100%;height:82rpx;margin-top:18rpx;padding:0 20rpx;border:1rpx solid #cbd5e1;border-radius:14rpx;font-size:25rpx}.refund-picker,.refund-total{min-height:82rpx;margin-top:12rpx;display:flex;align-items:center;border-bottom:1rpx solid #e2e8f0;font-size:25rpx}.refund-picker strong{margin-left:auto;margin-right:14rpx}.refund-total{justify-content:space-between}.refund-total strong{color:#e77800;font-size:31rpx}.sheet-actions{display:flex;gap:14rpx;margin-top:22rpx}.sheet-actions button{height:88rpx;border-radius:15rpx;display:flex;align-items:center;justify-content:center;font-size:26rpx;font-weight:680}.cancel{width:28%;border:1rpx solid #94a3b8;background:#fff}.confirm{flex:1;background:#0f766e;color:#fff}
</style>

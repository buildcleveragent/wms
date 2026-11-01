<template>
  <view class="page">
    <view v-if="error || localError" class="error">{{ localError || error }}</view>
    <view v-if="loading && !task" class="card">加载中…</view>
    <view v-else-if="!task" class="card"><text>盘点详情暂不可用</text><button @click="reload">重试</button></view>
    <template v-if="task">
      <view class="header card">
        <view class="task-no">{{ task.task_no }}</view>
        <view class="meta">{{ task.owner_name }} · {{ task.warehouse_name }}</view>
        <view :class="['status', { failed: task.posting_status === 'FAILED' }]">{{ countStatus(task) }}</view>
        <view class="meta">{{ task.blind ? '盲盘' : '明盘' }} · 第 {{ task.round_no || 1 }} 轮 · {{ task.line_count ?? lines.length }} 行</view>
        <view v-if="task.remark" class="meta">备注：{{ task.remark }}</view>
      </view>
      <view v-if="isDraft" class="card">
        <view class="line-title">盘点范围：{{ countScopes.find(s => s.id === task.scope)?.name }}</view>
        <view v-for="(label, key) in task.scope_labels" :key="key" class="meta">{{ scopeLabels[key] }}：{{ label }}</view>
        <view v-if="task.scope_payload?.location_prefix" class="meta">库位前缀：{{ task.scope_payload.location_prefix }}</view>
        <view class="meta">批次：{{ task.scope_payload?.batch_no || '全部批次' }}</view>
        <view class="meta">复盘阈值：{{ task.recount_threshold }} · 上限：{{ task.scope_payload?.max_lines }} 行</view>
        <view class="meta">{{ task.scope_payload?.exclude_zero_onhand ? '排除零库存' : '包含零库存' }}</view>
        <view class="hint">发布时按最新库存重新生成明细，并冻结所选范围。当前草稿未冻结库存。</view>
        <view v-if="truncated || task.scope_truncated" class="error">生成明细已达到范围上限并发生截断。发布时将重新检查，超限将拒绝发布。请取消草稿，缩小范围或提高上限后重建。</view>
      </view>
      <view v-if="isWorking" class="scan card">
        <input v-model="scanCode" placeholder="扫描或输入商品条码" @confirm="locateBarcode()"/><button size="mini" @click="quickScan">扫码</button>
      </view>
      <view v-for="line in lines" :key="line.id" :class="['line', 'card', `count-line-${line.id}`]">
        <view class="line-title">{{ (line.product_display_name || line.product_name) || line.product_sku }}</view>
        <view class="meta">{{ line.product_code || line.product_sku }}<text v-if="line.product_spec"> · {{ line.product_spec }}</text></view>
        <view class="meta">库位：{{ line.location_code }}　批次：{{ line.lot_no || '-' }}</view>
        <view v-if="line.qty_book !== undefined" class="meta">账面：{{ line.qty_book }}　差异：{{ line.qty_diff }}</view>
        <view v-if="!isDraft" class="entry">
          <input type="digit" v-model="line._qty" placeholder="实盘数量（可为0）" :disabled="!canRecord || busy || loading"/>
          <button v-if="canRecord" size="mini" :disabled="busy || line._qty === ''" @click="saveLine(line)">保存</button>
        </view>
        <view v-if="!isDraft" :class="['result', { done: line.count_status === 'COUNTED' }]">{{ line.count_status === 'COUNTED' ? `已盘：${line.qty_counted}` : '未盘' }}</view>
      </view>
      <view class="card actions">
        <template v-if="isDraft && manager">
          <button :disabled="busy || loading" @click="manage('cancel')">取消草稿</button>
          <button type="primary" :disabled="busy || loading" @click="manage('release')">确认发布</button>
        </template>
        <template v-else-if="isWorking">
          <button v-if="!task.can_record && !task.has_active_assignment" :disabled="busy || loading" @click="claim">认领任务</button>
          <button type="primary" :disabled="!canRecord || !allCounted || busy || hasUnsaved" @click="submit">完成并提交</button>
          <view v-if="hasUnsaved" class="hint">请先保存修改后的实盘数量。</view>
        </template>
        <template v-else-if="task.review_status === 'PENDING' && manager">
          <input v-model="note" class="note" maxlength="200" placeholder="审核备注；驳回时必填"/>
          <button :disabled="busy || loading" @click="manage('reject')">驳回</button><button type="primary" :disabled="busy || loading" @click="manage('approve')">审核通过</button>
        </template>
        <template v-else-if="canPost && manager">
          <view class="hint">{{ task.posting_status === 'FAILED' ? '上次过账失败，库存仍冻结，可重试。' : '审核已通过，确认后按盘盈盘亏调整库存。' }}</view>
          <button type="primary" :disabled="busy || loading" @click="manage('post')">{{ task.posting_status === 'FAILED' ? '重试过账' : '确认盘盈盘亏过账' }}</button>
        </template>
        <button :disabled="busy || loading" @click="backToList">返回{{ manager ? '盘点列表' : '盘点任务' }}</button>
      </view>
    </template>
  </view>
</template>
<script setup>
import { computed, ref } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { api, createIdempotencyUuid } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'
import { countScopes, countStatus, hasUnsavedCounts, useCountSession } from '@/utils/counting'
const auth = useAuth(), taskId = ref(0), truncated = ref(false), localError = ref(''), scanCode = ref(''), note = ref('')
const confirm = content => new Promise(resolve => uni.showModal({ title: '确认操作', content, success: result => resolve(result.confirm), fail: () => resolve(false) }))
const session = useCountSession({ api, auth, confirm })
const { task, lines, loading, busy, error, manager } = session
const isDraft = computed(() => ['DRAFT', 'READY'].includes(task.value?.status))
const isWorking = computed(() => ['RELEASED', 'IN_PROGRESS'].includes(task.value?.status))
const canRecord = computed(() => isWorking.value && Boolean(task.value?.can_record))
const canPost = computed(() => task.value?.status === 'COMPLETED' && task.value?.review_status === 'APPROVED' && ['PENDING', 'FAILED'].includes(task.value?.posting_status))
const allCounted = computed(() => lines.value.length > 0 && lines.value.every(line => line.count_status === 'COUNTED'))
const hasUnsaved = computed(() => hasUnsavedCounts(lines.value))
const scopeLabels = { subwarehouse_id: '子仓', location_id: '库位', product_id: '商品', zone_type: '库区' }
const { quickScan } = useBarcodeScanner({ onScan: code => { scanCode.value = code; locateBarcode() } })
async function reload() { if (taskId.value) await session.load({ id: taskId.value, refreshAuth: true }) }
function locateBarcode() {
  const code = String(scanCode.value || '').trim().toUpperCase()
  if (!code) return
  const matches = lines.value.filter(item => [item.product_sku, item.product_code, item.unit_barcode, item.carton_barcode, item.gtin].some(value => value && String(value).trim().toUpperCase() === code))
  if (!matches.length) { uni.showToast({ title: '条码不属于当前盘点任务', icon: 'none' }); return }
  uni.pageScrollTo({ selector: `.count-line-${matches[0].id}`, duration: 200 })
  uni.showToast({ title: matches.length > 1 ? '存在多个库位或批次，请核对明细后录入' : `已定位 ${matches[0].product_display_name || matches[0].product_name}`, icon: 'none' })
}
async function claim() { if (await session.act('claim')) await reload() }
async function saveLine(line) {
  localError.value = ''
  if (!/^\d+(\.\d{1,4})?$/.test(String(line._qty))) { localError.value = '实盘数量须为非负数，最多四位小数'; return }
  if (!line._clientSeq || line._clientQty !== line._qty) { line._clientSeq = createIdempotencyUuid(); line._clientQty = line._qty }
  const result = await session.act('record', { line_id: line.id, qty_counted: line._qty, client_seq: line._clientSeq, barcode: scanCode.value })
  if (!result) return
  line.qty_counted = result.qty_counted; line._qty = result.qty_counted; line.count_status = 'COUNTED'
  if (line.qty_diff !== undefined) line.qty_diff = result.qty_diff
  line._clientSeq = ''; scanCode.value = ''; task.value.status = 'IN_PROGRESS'
}
async function submit() {
  if (!canRecord.value || !allCounted.value || hasUnsaved.value) return
  const result = await session.act('submit', {}, '确认所有明细已盘点并提交？')
  if (!result) return
  if (result.outcome === 'RECOUNT_RELEASED' && result.next_task_id) { uni.redirectTo({ url: `/pages/inventory/stocktake/detail?task_id=${result.next_task_id}` }); return }
  await reload()
  if (result.outcome === 'POSTING_FAILED') localError.value = '自动过账失败，请主管从待过账列表重试。'
}
async function manage(action) {
  localError.value = ''
  if (action === 'reject' && !note.value.trim()) { localError.value = '请填写驳回原因'; return }
  const messages = { release: '发布将按最新库存重新生成明细并冻结所选范围，是否确认？', cancel: '确认取消此草稿？', approve: '确认审核通过？之后仍需完成过账。', reject: '确认驳回盘点结果？', post: '确认按盘盈盘亏调整库存并完成过账？' }
  const result = await session.act(action, { note: note.value.trim() }, messages[action])
  if (result) { note.value = ''; await reload() }
  else if (error.value && !session.denied.value) {
    const reason = error.value
    await reload()
    if (task.value) localError.value = reason
  }
}
function backToList() {
  const mode = manager.value ? (isDraft.value ? 'draft' : canPost.value ? 'posting' : task.value?.review_status === 'PENDING' ? 'review' : task.value?.posting_status === 'POSTED' ? 'posting' : 'work') : 'work'
  uni.redirectTo({ url: `/pages/inventory/stocktake/index?mode=${mode}` })
}
onLoad(query => { taskId.value = Number(query?.task_id || 0); truncated.value = query?.truncated === '1' })
onShow(reload)
onHide(session.invalidate)
onUnload(session.dispose)
</script>
<style scoped>
.page{padding:24rpx;min-height:100vh;background:#f5f7fa;box-sizing:border-box}.card{background:white;border-radius:16rpx;padding:24rpx;margin-bottom:20rpx}.task-no,.line-title{font-size:30rpx;font-weight:700;overflow-wrap:anywhere}.meta,.hint{color:#64748b;font-size:25rpx;line-height:1.6;margin-top:8rpx;overflow-wrap:anywhere}.status{color:#2563eb;margin-top:14rpx;font-weight:600}.scan,.entry{display:flex;align-items:center;gap:16rpx}.scan input,.entry input{min-width:0;flex:1;border:1rpx solid #cbd5e1;border-radius:10rpx;padding:16rpx}.entry{margin-top:18rpx}.scan button,.entry button{margin:0}.result{font-size:24rpx;color:#b91c1c;margin-top:12rpx}.done{color:#047857}.actions{display:flex;flex-direction:column;gap:16rpx}.actions button{width:100%}.note{padding:18rpx;border:1rpx solid #cbd5e1;border-radius:10rpx}.error{background:#fff1f2;color:#b91c1c;padding:20rpx;margin-bottom:20rpx;border-radius:12rpx;line-height:1.6}.failed{color:#b91c1c}
</style>

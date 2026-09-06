<template>
  <view class="page" v-if="task">
    <view class="header card">
      <view class="task-no">{{ task.task_no }}</view>
      <view class="meta">{{ task.owner_name }} · {{ task.warehouse_name }}</view>
      <view class="meta">{{ task.status }} / {{ task.review_status }} / {{ task.posting_status }}</view>
    </view>

    <view v-if="isWorking" class="scan card">
      <input v-model="scanCode" placeholder="扫描或输入商品条码" @confirm="locateBarcode" />
      <button @click="quickScan">扫码</button>
    </view>

    <view v-for="line in lines" :key="line.id" :class="['line', 'card', `count-line-${line.id}`]">
      <view class="line-title">{{ line.product_name || line.product_sku }}</view>
      <view class="meta">库位：{{ line.location_code }}　批次：{{ line.lot_no || '-' }}</view>
      <view v-if="line.qty_book !== undefined" class="meta">
        账面：{{ line.qty_book }}　差异：{{ line.qty_diff }}
      </view>
      <view class="entry">
        <input
          type="digit"
          v-model="line._qty"
          placeholder="实盘数量（可为0）"
          :disabled="!canRecord || savingId === line.id"
        />
        <button
          v-if="canRecord"
          :disabled="savingId === line.id || line._qty === ''"
          @click="saveLine(line)"
        >{{ savingId === line.id ? '保存中' : '保存' }}</button>
      </view>
      <view :class="['result', { done: line.count_status === 'COUNTED' }]">
        {{ line.count_status === 'COUNTED' ? `已盘：${line.qty_counted}` : '未盘' }}
      </view>
    </view>

    <view class="footer" v-if="isWorking">
      <button v-if="!task.can_record" @click="claim">认领任务</button>
      <button class="primary" :disabled="!task.can_record || !allCounted || submitting" @click="submit">
        {{ submitting ? '提交中...' : '完成并提交' }}
      </button>
    </view>
    <view class="footer" v-else-if="task.review_status === 'PENDING'">
      <input v-model="note" placeholder="审核备注；驳回时必填" />
      <button class="danger" @click="reject">驳回</button>
      <button class="primary" @click="approve">审核通过</button>
    </view>
    <view class="footer" v-else-if="task.review_status === 'APPROVED' && task.posting_status !== 'POSTED'">
      <button class="primary" @click="post">确认盘盈盘亏过账</button>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api, createIdempotencyUuid } from '@/utils/request'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'

const taskId = ref<number | null>(null)
const task = ref<any>(null)
const lines = ref<any[]>([])
const savingId = ref<number | null>(null)
const submitting = ref(false)
const scanCode = ref('')
const note = ref('')
const { quickScan } = useBarcodeScanner({
  onScan: (code: string) => { scanCode.value = code; locateBarcode(code) },
})

const isWorking = computed(() => ['RELEASED', 'IN_PROGRESS'].includes(task.value?.status))
const canRecord = computed(() => isWorking.value && Boolean(task.value?.can_record))
const allCounted = computed(() => lines.value.length > 0 && lines.value.every((line: any) => line.count_status === 'COUNTED'))

async function load() {
  if (!taskId.value) return
  const [head, detail]: any[] = await Promise.all([
    api.countTaskDetail(taskId.value),
    api.countTaskLines(taskId.value),
  ])
  task.value = head
  const list = Array.isArray(detail) ? detail : (detail?.results || [])
  lines.value = list.map((line: any) => ({
    ...line,
    _qty: line.count_status === 'COUNTED' ? String(line.qty_counted) : '',
  }))
}

function locateBarcode(value?: string) {
  const code = String(value || scanCode.value || '').trim().toUpperCase()
  if (!code) return
  const line = lines.value.find((item: any) => [
    item.product_sku, item.product_code, item.unit_barcode, item.carton_barcode, item.gtin,
  ].some((candidate) => String(candidate || '').trim().toUpperCase() === code))
  if (!line) {
    uni.showToast({ title: '条码不属于当前盘点任务', icon: 'none' })
    return
  }
  scanCode.value = code
  uni.pageScrollTo({ selector: `.count-line-${line.id}`, duration: 200 })
  uni.showToast({ title: `已定位 ${line.product_name}`, icon: 'none' })
}

async function claim() {
  await api.claimCountTask(taskId.value)
  uni.showToast({ title: '认领成功', icon: 'success' })
  await load()
}

async function saveLine(line: any) {
  savingId.value = line.id
  try {
    const currentQty = String(line._qty)
    if (!line._clientSeq || line._clientQty !== currentQty) {
      line._clientSeq = createIdempotencyUuid()
      line._clientQty = currentQty
    }
    await api.recordCount(taskId.value, {
      line_id: line.id,
      qty_counted: line._qty,
      client_seq: line._clientSeq,
      barcode: scanCode.value,
    })
    line._clientSeq = ''
    line._clientQty = ''
    scanCode.value = ''
    await load()
  } catch (error: any) {
    uni.showToast({ title: error?.data?.detail || '保存盘点数量失败', icon: 'none' })
  } finally {
    savingId.value = null
  }
}

async function submit() {
  submitting.value = true
  try {
    const result: any = await api.submitCountTask(taskId.value)
    if (result.outcome === 'RECOUNT_RELEASED' && result.next_task_id) {
      uni.redirectTo({ url: `/pages/inventory/stocktake/detail?task_id=${result.next_task_id}` })
      return
    }
    if (result.outcome === 'POSTING_FAILED') {
      uni.showToast({ title: '自动过账失败，请主管重试', icon: 'none' })
      await load()
      return
    }
    uni.showToast({ title: result.outcome === 'AUTO_POSTED_NO_DIFF' ? '盘点无差异，已完成' : '已提交主管审核', icon: 'none' })
    await load()
  } finally {
    submitting.value = false
  }
}

async function approve() { await api.approveCountTask(taskId.value, { note: note.value }); await load() }
async function reject() { await api.rejectCountTask(taskId.value, { note: note.value }); await load() }
async function post() { await api.postCountTask(taskId.value, { note: note.value }); await load() }

onLoad((query: any) => { taskId.value = Number(query?.task_id || 0) })
onMounted(async () => {
  try { await load() } catch (error) { console.error(error); uni.showToast({ title: '加载盘点详情失败', icon: 'none' }) }
})
</script>

<style scoped>
.page { padding: 20rpx; padding-bottom: 160rpx; background: #f5f7fa; min-height: 100vh; }
.card { background: #fff; border-radius: 16rpx; padding: 22rpx; margin-bottom: 16rpx; }
.task-no, .line-title { font-weight: 700; font-size: 30rpx; }
.meta { color: #64748b; font-size: 24rpx; margin-top: 8rpx; }
.scan, .entry, .footer { display: flex; gap: 14rpx; align-items: center; }
.scan input, .entry input, .footer input { flex: 1; border: 1rpx solid #cbd5e1; border-radius: 10rpx; padding: 16rpx; }
.result { margin-top: 10rpx; color: #dc2626; }
.result.done { color: #059669; }
.footer { position: fixed; left: 0; right: 0; bottom: 0; background: #fff; padding: 18rpx 24rpx; box-shadow: 0 -2rpx 12rpx rgba(15,23,42,.12); }
.footer button { flex: 1; }
.primary { background: #2563eb; color: #fff; }
.danger { background: #dc2626; color: #fff; }
</style>

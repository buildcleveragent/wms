<template> 
  <view class="page">
    <!-- 任务头信息 -->
    <view class="card-first">
      <view class="row-first">
        <text class="title">拣货任务</text>
      </view>
      <view class="row-first">
        <text>任务号：{{ task?.task_no || taskId }}</text>
      </view>
      <view class="row-meta">
        <text>货主：{{ task?.owner_name || '-' }}</text>
        <text style="margin-left: 24rpx;">仓库：{{ task?.warehouse_name || '-' }}</text>
      </view>
      <view class="row-meta">
        <text>状态：{{ task?.status }}</text>
        <text style="margin-left: 24rpx;">复核：{{ task?.review_status || '-' }}</text>
        <text style="margin-left: 24rpx;">过账：{{ task?.posting_status || '-' }}</text>
      </view>
      <view v-if="task?.is_warehouse_assisted" class="assisted-badge">仓库代办出库</view>
    </view>

    <!-- 扫码 + 数量 -->
    <view v-if="isPickable" class="scan-bar">
			    <input
        class="input flex-input"
        v-model="scanBarcode"
        placeholder="扫描或输入条码"
        @confirm="submitScan()"
      />
      <input
        class="input qty-input"
        type="number"
        v-model="scanQty"
        @confirm="submitScan()"
      />
      <button class="btn-outline" :disabled="scanning || adjustingCount > 0" @click="handleScan">扫码</button>
      <button class="btn-outline" :disabled="scanning || adjustingCount > 0" @click="submitScan()">
        {{ scanning ? '录入中…' : '录入' }}
      </button>
    </view>

    <!-- 任务行列表 -->
    <view class="content">
      <view
        v-for="(ln, i) in lines"
        :key="ln.id ?? i"
        :class="['row item', { odd: i % 2 === 0 }]"
      >
        <view class="col-info">
          <text class="name">{{ ln.product_name || ln.product_sku || ln.product_id }}</text>
          <view class="meta">
            <text>货位：{{ ln.from_loc_code || '-' }}</text>
          </view>

          <!-- 原有计划/已拣显示 -->
          <view class="qty-row">
            <text>计划：{{ formatQty(ln.qty_plan) }}</text>
            <text style="margin-left: 24rpx;">已拣：{{ formatQty(ln.qty_done) }}</text>
          </view>

		  <view class="qty-edit-row">
		    <text class="qty-edit-label">当前拣货数：</text>
		    <input
		      class="line-qty-input"
		      type="number"
		      :value="formatQty(ln.qty_done)"
		      placeholder=""
		      @input="(e) => onEditQty(e, ln)"
				  @blur="applyManualQty(ln)"
				  :disabled="!isPickable || scanning || adjustingLineIds.has(ln.id)"

		    />
		  </view>
        </view>
      </view>
    </view>

    <!-- 底部完成按钮 -->
    <view class="footer" v-if="task">
      <button
        v-if="isPickable"
        class="btn-primary"
        :disabled="!allDone || submittingReview || scanning"
        @click="createReviewTask"
      >
        {{ submittingReview ? '正在保存并提交…' : '完成拣货' }}
      </button>
      <button
        v-else-if="canSelfReview"
        class="btn-danger"
        :disabled="posting || confirmingPost"
        @click="confirmAndPost"
      >
        {{ posting ? '正在复核过账…' : task.review_status === 'APPROVED' ? '重试确认出库' : '复核并确认出库' }}
      </button>
      <view v-else-if="isPosted" class="completed-text">本任务已完成复核并过账</view>
      <view v-else class="completed-text">拣货已提交，等待复核</view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'
import { useAuth } from '@/store/auth'

const taskId = ref<number | null>(null)
const auth = useAuth()
const task = ref<any | null>(null)
const lines = ref<any[]>([])

const scanBarcode = ref('')
const scanQty = ref<string>('1')
const loading = ref(false)
const submittingReview = ref(false)
const posting = ref(false)
const confirmingPost = ref(false)
const scanning = ref(false)
const adjustingCount = ref(0)
const adjustingLineIds = new Set<number>()
const dirtyLineIds = new Set<number>()
const adjustmentPromises = new Map<number, Promise<boolean>>()

// 扫描钩子
const { lastScan, quickScan, setScanCallback, initScanner, unRegisterBroadcast } =
  useBarcodeScanner()

const allDone = computed(() => {
  if (!lines.value.length) return false
  return lines.value.every((ln: any) =>
    Number(ln.qty_done || 0) >= Number(ln.qty_plan || 0)
  )
})

const isPickable = computed(() => ['RESERVED', 'RELEASED', 'IN_PROGRESS'].includes(task.value?.status))
const isPosted = computed(() => task.value?.posting_status === 'POSTED')
const canSelfReview = computed(() => {
  if (task.value?.status !== 'COMPLETED' || task.value?.can_self_review !== true) return false
  const reviewStatus = task.value?.review_status
  const postingStatus = task.value?.posting_status
  return (
    (reviewStatus === 'PENDING' && ['NOT_READY', 'PENDING'].includes(postingStatus)) ||
    (reviewStatus === 'APPROVED' && ['PENDING', 'FAILED'].includes(postingStatus))
  )
})

// 每次扫描生成一个唯一的 client_seq
function genClientSeq(): string {
  return Date.now().toString() + '-' + Math.random().toString(36).slice(2, 8)
}

async function loadTask() {
  if (!taskId.value) return
  try {
    const res: any = await api.pickTaskDetail(taskId.value)
    task.value = res
  } catch (e) {
    console.error(e)
    uni.showToast({ title: '加载任务头失败', icon: 'none' })
  }
}

async function loadLines() {
  if (!taskId.value) return
  loading.value = true
  try {
    const res: any = await api.pickTaskLines(taskId.value)
    const list = Array.isArray(res) ? res : (res.results || [])
    // ✅ 初始化每行的可编辑拣货数
    lines.value = list.map((ln: any) => ({
      ...ln,
      _edit_qty_done: ln.qty_done ?? 0,
    }))
    dirtyLineIds.clear()
  } catch (e) {
    console.error(e)
    uni.showToast({ title: '加载任务行失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}


function applyManualQty(ln: any): Promise<boolean> {
  const lineId = Number(ln?.id)
  if (!lineId || !taskId.value || !isPickable.value || scanning.value) {
    return Promise.resolve(false)
  }

  const existing = adjustmentPromises.get(lineId)
  if (existing) return existing
  if (!dirtyLineIds.has(lineId)) return Promise.resolve(true)

  const raw = ln.qty_done
  const val = Number(raw)
  if (!Number.isFinite(val) || val < 0) {
    uni.showToast({ title: '请输入合法的拣货数量', icon: 'none' })
    return Promise.resolve(false)
  }

  const request = (async (): Promise<boolean> => {
    adjustingLineIds.add(lineId)
    adjustingCount.value += 1
    try {
      const res: any = await api.adjustPickLineQty(taskId.value, {
        line_id: lineId,
        final_qty_done: val,
        client_seq: genClientSeq(),
      })
      ln.qty_done = res.qty_done
      ln.edit_qty = res.qty_done
      dirtyLineIds.delete(lineId)
      return true
    } catch (e: any) {
      const msg = e?.data?.detail || e?.data?.message || '调整拣货数量失败'
      uni.showToast({ title: String(msg), icon: 'none' })
      await loadLines()
      return false
    } finally {
      adjustingLineIds.delete(lineId)
      adjustingCount.value = Math.max(0, adjustingCount.value - 1)
      adjustmentPromises.delete(lineId)
    }
  })()

  adjustmentPromises.set(lineId, request)
  return request
}

async function flushManualQtyEdits(): Promise<boolean> {
  for (const ln of lines.value) {
    const lineId = Number(ln?.id)
    if (!lineId || (!dirtyLineIds.has(lineId) && !adjustmentPromises.has(lineId))) continue
    if (!await applyManualQty(ln)) return false
  }
  const remaining = Array.from(adjustmentPromises.values())
  if (remaining.length) {
    const results = await Promise.all(remaining)
    if (results.some((saved) => !saved)) return false
  }
  return adjustmentPromises.size === 0
}



async function submitScan(barcodeOverride?: string) {
  if (!taskId.value || !isPickable.value || scanning.value || adjustingCount.value > 0) return
  const code = (barcodeOverride || scanBarcode.value || '').trim()
  if (!code) {
    uni.showToast({ title: '请先扫描或输入条码', icon: 'none' })
    return
  }
  const q = Number(scanQty.value) || 1

  scanning.value = true
  try {
    const res: any = await api.scanPick(taskId.value, {
      barcode: code,
      qty: q,
      client_seq: genClientSeq(), // 每次扫描一个新的 client_seq
    })

    const lineId = res.line_id
    if (lineId) {
      const ln = lines.value.find((x: any) => x.id === lineId)
      // 优先用 res.line.qty_done
      let newQtyDone: number | undefined
      if (res.line && typeof res.line.qty_done !== 'undefined') {
        newQtyDone = Number(res.line.qty_done)
      } else if (typeof res.qty_done !== 'undefined') {
        newQtyDone = Number(res.qty_done)
      }

      if (ln && !Number.isNaN(newQtyDone as number)) {
        ln.qty_done = newQtyDone
        ln._edit_qty_done = formatQty(newQtyDone) // ✅ 同步到输入框
      }
    }

    uni.showToast({ title: '已记录拣货', icon: 'none' })
    scanBarcode.value = ''
  } catch (err: any) {
    console.error(err)
    const msg = err?.data?.detail || err?.data?.message || '拣货失败'
    uni.showToast({ title: String(msg), icon: 'none' })
  } finally {
    scanning.value = false
  }
}

// 点击“扫码”按钮
function handleScan() {
  if (!isPickable.value || scanning.value || adjustingCount.value > 0) return
  quickScan()
}

// 注册扫描回调：扫码→直接提交
setScanCallback((barcode: string) => {
  console.log('拣货页面收到条码:', barcode)
  scanBarcode.value = barcode
  submitScan(barcode)
})

async function postTask() {
  if (!taskId.value || posting.value) return
  posting.value = true
  try {
    const res: any = await api.postPickTask(taskId.value)
    uni.showToast({
      title: res?.message || '拣货已过账',
      icon: 'none',
    })
    setTimeout(() => {
      uni.navigateBack()
    }, 800)
  } catch (err: any) {
    console.error(err)
    if (Number(err?.statusCode || err?.code) === 403 && task.value?.is_warehouse_assisted) {
      auth.invalidateAssistedCapability()
    }
    const msg = err?.data?.detail || err?.data?.message || '过账失败'
    uni.showToast({ title: String(msg), icon: 'none' })
  } finally {
    posting.value = false
  }
}

function confirmAndPost() {
  if (!canSelfReview.value || posting.value || confirmingPost.value) return
  confirmingPost.value = true
  uni.showModal({
    title: '确认单人复核并出库',
    content: '你将以同一仓库操作员身份完成复核和库存过账。请确认商品、数量和库位均已核对无误。',
    confirmText: '确认出库',
    confirmColor: '#c62828',
    success: (result) => {
      if (result.confirm) postTask()
    },
    complete: () => {
      confirmingPost.value = false
    },
  })
}

async function createReviewTask() {
  if (
    !taskId.value ||
    !isPickable.value ||
    submittingReview.value ||
    scanning.value
  ) return

  submittingReview.value = true
  try {
    // 点击按钮会先触发当前数量输入框的 blur。等待该保存请求，或者
    // 主动保存尚未失焦的编辑，避免第一次点击只保存数量、第二次才提交。
    if (!await flushManualQtyEdits()) return
    if (!allDone.value) {
      uni.showToast({ title: '还有未拣完的行，不能提交复核', icon: 'none' })
      return
    }

    const res: any = await api.createPickReviewTask(taskId.value)
    uni.showToast({
      title: res?.message || '拣货完成，已创建复核任务',
      icon: 'none',
    })
    await loadTask()
    await loadLines()
  } catch (err: any) {
    console.error(err)
    const msg = err?.data?.detail || err?.data?.message || '提交复核任务失败'
    uni.showToast({ title: String(msg), icon: 'none' })
    // 请求超时但服务端已成功时，刷新后可直接进入第二阶段。
    await loadTask()
    await loadLines()
  } finally {
    submittingReview.value = false
  }
}

// function formatQty(val: any): string {
//   if (val === null || val === undefined || val === '') return ''

//   const n = Number(val)
//   if (Number.isNaN(n)) {
//     // 非数字，就原样返回字符串
//     return String(val)
//   }

//   // 是整数，就只显示整数部分
//   if (Number.isInteger(n)) {
//     return n.toString()
//   }

//   // 有小数，就正常显示（会自动去掉多余的 0，比如 10.50 -> 10.5）
//   return n.toString()
// }


function formatQty(value) {
  if (value === null || value === undefined || value === '') return '0'

  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)

  return String(Number(n.toFixed(4)))
}

function onEditQty(e: any, ln: any) {
  // uni-app 的 input 事件里，值在 e.detail.value
  const raw = e?.detail?.value ?? e?.target?.value ?? ''
  const val = Number(raw)

  if (Number.isNaN(val) || val < 0) {
    uni.showToast({ title: '请输入合法的拣货数量', icon: 'none' })
    // 输入非法时，可以归零或保持原值，这里我保持原值：
    return
  }

  // 真正参与业务 / allDone 计算的是 qty_done
  ln.qty_done = val
  if (ln?.id) dirtyLineIds.add(Number(ln.id))
}

onLoad((opts: any) => {
  const id = Number(opts?.task_id)
  if (!id) {
    uni.showToast({ title: '缺少任务ID', icon: 'none' })
    return
  }
  taskId.value = id
  loadTask()
  loadLines()
})

onMounted(() => {
  initScanner()
})

onUnmounted(() => {
  unRegisterBroadcast()
})



</script>

<style scoped>
.page {
  padding: 16rpx;
}
.card-first {
  background: #fff;
  border-radius: 16rpx;
  padding: 16rpx;
  margin-bottom: 16rpx;
  box-shadow: 0 2rpx 6rpx rgba(15, 23, 42, 0.08);
}
.row-first {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8rpx;
}
.row-meta {
  font-size: 24rpx;
  color: #475569;
  margin-top: 4rpx;
}
.title {
  font-size: 32rpx;
  font-weight: 600;
}
.scan-bar {
  flex-direction: row;
  align-items: center;
  display: flex;
  gap: 8rpx;
  margin-bottom: 16rpx;
}
.input {
  border: 1rpx solid #e5e7eb;
  border-radius: 8rpx;
  padding: 8rpx 12rpx;
  background: #fff;
}
.flex-input {
  flex: 1;
}
.qty-input {
  width: 120rpx;
  text-align: center;
}
.btn-outline {
  padding: 8rpx 16rpx;
  border-radius: 8rpx;
  border: 1rpx solid #cbd5e1;
  background: #fff;
  font-size: 24rpx;
}
.content {
  margin-top: 8rpx;
}
.row.item {
  flex-direction: row;
  display: flex;
  padding: 12rpx;
  border-radius: 12rpx;
  background: #fff;
  margin-bottom: 8rpx;
}
.row.item.odd {
  background: #f8fafc;
}
.col-info {
  flex: 1;
}
.name {
  font-size: 28rpx;
  font-weight: 500;
}
.meta {
  font-size: 24rpx;
  color: #64748b;
  margin-top: 4rpx;
}
.qty-row {
  margin-top: 4rpx;
  font-size: 24rpx;
}

/* ✅ 新增样式：行内手工输入拣货数 */
.qty-edit-row {
  margin-top: 6rpx;
  font-size: 24rpx;
  display: flex;
  align-items: center;
}
.qty-edit-label {
  margin-right: 8rpx;
  color: #64748b;
}
.line-qty-input {
  flex: 0 0 180rpx;
  border: 1rpx solid #e5e7eb;
  border-radius: 8rpx;
  padding: 4rpx 8rpx;
  background: #fff;
  text-align: center;
}

.footer {
  margin-top: 24rpx;
}
.btn-primary {
  width: 100%;
  padding: 12rpx 0;
  border-radius: 12rpx;
  background: #0f766e;
  color: #fff;
  text-align: center;
  font-size: 28rpx;
}
.btn-primary:disabled {
  background: #94a3b8;
}
.assisted-badge {
  display: inline-block;
  margin-top: 10rpx;
  padding: 5rpx 12rpx;
  border-radius: 999rpx;
  color: #9a3412;
  background: #ffedd5;
  font-size: 22rpx;
}
.btn-danger {
  width: 100%;
  padding: 12rpx 0;
  border-radius: 12rpx;
  color: #fff;
  background: #c62828;
  text-align: center;
  font-size: 28rpx;
}
.btn-danger:disabled {
  background: #94a3b8;
}
.completed-text {
  padding: 20rpx;
  border-radius: 12rpx;
  color: #475569;
  background: #f1f5f9;
  text-align: center;
}
</style>

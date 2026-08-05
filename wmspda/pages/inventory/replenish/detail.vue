<template>
  <view class="page">
    <view v-if="loading" class="state">正在加载…</view>
    <template v-else-if="task">
      <view class="card">
        <text class="title">{{ task.task_no }}</text>
        <text class="meta">{{ task.owner_name }} · {{ task.warehouse_name }}</text>
        <text class="meta">状态：{{ task.status }} · 过账：{{ task.posting_status }}</text>
        <view class="actions">
          <button v-if="task.can_claim" size="mini" @click="claim">领取任务</button>
          <button v-if="task.can_start" type="primary" size="mini" @click="start">开始补货</button>
          <button v-if="canRetry && task.posting_status === 'FAILED'" size="mini" @click="retryPosting">重试过账</button>
        </view>
      </view>

      <view v-for="line in task.lines" :key="line.id" class="card">
        <text class="product">{{ line.product_name || line.product_code }}</text>
        <text class="meta">{{ line.from_location_code }} → {{ line.to_location_code }}</text>
        <text class="meta">计划 {{ line.qty_plan }} · 已完成 {{ line.qty_done }} · 剩余 {{ line.qty_pending }}</text>
        <text v-if="line.lot_no" class="meta">批次 {{ line.lot_no }} · 效期 {{ line.exp_date || '-' }}</text>
        <template v-if="task.can_record && !line.finished_at">
          <input v-model="forms[line.id].from" class="input" placeholder="扫描/输入来源库位码" />
          <input v-model="forms[line.id].product" class="input" placeholder="扫描/输入商品码" />
          <input v-model="forms[line.id].to" class="input" placeholder="扫描/输入目标库位码" />
          <input v-if="line.serial_control" v-model="forms[line.id].serial" class="input" placeholder="扫描序列号" />
          <input v-model="forms[line.id].qty" class="input" type="digit" placeholder="本次补货数量" />
          <button type="primary" :loading="saving === line.id" @click="record(line)">确认补货</button>
        </template>
      </view>
    </template>
    <view v-else class="state">任务不存在或无权访问</view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api, createIdempotencyUuid } from '@/utils/request'

const auth = useAuth()
const taskId = ref(null)
const task = ref(null)
const forms = ref({})
const loading = ref(false)
const saving = ref(null)
const canRetry = computed(() => auth.canRetryReplenishmentPosting)

function hydrate(value) {
  task.value = value
  const next = {}
  for (const line of value?.lines || []) {
    next[line.id] = forms.value[line.id] || {
      from: '', to: '', product: '', serial: '', qty: line.serial_control ? '1' : line.qty_pending,
      requestId: createIdempotencyUuid(),
    }
  }
  forms.value = next
}

async function load() {
  loading.value = true
  try { hydrate(await api.replenishmentTask(taskId.value)) }
  catch (_) { task.value = null }
  finally { loading.value = false }
}

async function claim() { hydrate(await api.claimReplenishmentTask(taskId.value)) }
async function start() { hydrate(await api.startReplenishmentTask(taskId.value)) }
async function retryPosting() { hydrate(await api.retryReplenishmentPosting(taskId.value)) }

async function record(line) {
  const form = forms.value[line.id]
  if (!form.from || !form.to || !form.product || Number(form.qty) <= 0) {
    uni.showToast({ title: '请完整扫描库位、商品并填写数量', icon: 'none' }); return
  }
  saving.value = line.id
  try {
    const response = await api.recordReplenishment(taskId.value, {
      request_id: form.requestId,
      line_id: line.id,
      from_location_code: form.from,
      to_location_code: form.to,
      product_code: form.product,
      serial_no: form.serial,
      qty: Number(form.qty),
    })
    hydrate(response.task)
    uni.showToast({ title: '补货已记录', icon: 'success' })
  } finally { saving.value = null }
}

onLoad(async (query) => {
  taskId.value = Number(query.task_id)
  await auth.loadProfile({ force:true })
  await load()
})
</script>

<style scoped>
.page { min-height:100vh; padding:24rpx; background:#f5f7fb; box-sizing:border-box; }
.card { margin-bottom:18rpx; padding:24rpx; background:#fff; border-radius:16rpx; }
.title,.product { display:block; color:#172033; font-size:31rpx; font-weight:700; }
.meta { display:block; margin-top:10rpx; color:#65758b; font-size:24rpx; }
.input { height:72rpx; margin-top:16rpx; padding:0 18rpx; border:1rpx solid #d7dfeb; border-radius:12rpx; }
.actions { display:flex; gap:14rpx; margin-top:18rpx; }
.actions button { margin:0; }
button { margin-top:18rpx; }
.state { padding:100rpx 0; text-align:center; color:#748197; }
</style>

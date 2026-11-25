<template>
  <view class="archive">
    <text v-if="!allowed" class="message">当前账号没有完整档案导出权限。</text>
    <template v-else>
      <view class="panel">
        <text class="heading">货主业务档案</text>
        <text class="note">经营统计、库存、业务单据和账单将保存在同一个 Excel。当前库存按生成时点统计。</text>
        <view class="controls">
          <button size="mini" :class="{selected: !custom}" @click="custom=false">全部历史</button>
          <button size="mini" :class="{selected: custom}" @click="custom=true">自选日期</button>
        </view>
        <view v-if="custom" class="controls">
          <picker mode="date" :value="start" @change="start=$event.detail.value"><button size="mini">{{ start || '开始日期' }}</button></picker>
          <text>至</text>
          <picker mode="date" :value="end" @change="end=$event.detail.value"><button size="mini">{{ end || '结束日期' }}</button></picker>
        </view>
        <button type="primary" :disabled="busy" @click="create">{{ busy ? '正在提交…' : '导出完整 Excel' }}</button>
      </view>
      <view class="panel">
        <view class="heading-row"><text class="heading">我的导出</text><button size="mini" @click="load">刷新</button></view>
        <text class="note">可离开此页，生成后 24 小时内可返回下载。</text>
        <text v-if="error" class="message">{{ error }}</text>
        <text v-if="!jobs.length && !error" class="note">暂无导出记录</text>
        <view v-for="job in jobs" :key="job.id" class="job">
          <view><text>{{ range(job) }}</text><text class="note">{{ job.created_at?.slice(0,10) }} · {{ labels[job.status] }}</text></view>
          <button v-if="job.status==='ready'" size="mini" :disabled="downloading" @click="download(job)">下载</button>
          <button v-if="job.status==='failed'" size="mini" :disabled="busy" @click="submit(job.date_from,job.date_to)">重新生成</button>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { onHide, onShow } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { downloadAuthenticatedFile, request } from '@/utils/request'

const auth = useAuth()
const allowed = computed(() => (auth.isOwnerManager || auth.isOwnerFinance)
  && auth.capabilities?.can_export_owner_archive === true)
const identity = computed(() => JSON.stringify([auth.user?.id, auth.user?.owner_id, allowed.value]))
const url = '/api/reports/owner-archives/'
const labels = { queued: '排队中', running: '生成中', ready: '可下载', failed: '生成失败' }
const jobs = ref([])
const custom = ref(false)
const start = ref('')
const end = ref('')
const busy = ref(false)
const downloading = ref(false)
const error = ref('')
let visible = false
let epoch = 0
let revision = 0
let timer
let requestedJobId

function range(job) {
  if (!job.date_from && !job.date_to) return '全部历史'
  return [job.date_from || '最早记录', job.date_to || '最新记录'].join(' 至 ')
}

function invalidate() {
  epoch++
  revision++
  clearTimeout(timer)
  requestedJobId = null
  busy.value = false
  downloading.value = false
}

async function download(job) {
  if (!visible || !allowed.value || downloading.value) return
  const tag = epoch
  downloading.value = true
  try {
    await downloadAuthenticatedFile(
      url + job.id + '/download/',
      '货主业务档案-' + range(job) + '-' + job.id + '.xlsx',
      { excel: true, isCurrent: () => tag === epoch && visible && allowed.value },
    )
  } catch (e) {
    if (tag === epoch) error.value = e.message || '下载失败，请重试'
  } finally {
    if (tag === epoch) downloading.value = false
  }
}

async function load() {
  if (!visible || !allowed.value) return
  clearTimeout(timer)
  const tag = epoch
  const sequence = ++revision
  const isCurrent = () => tag === epoch && sequence === revision
  try {
    const data = await request({ url })
    if (!isCurrent()) return
    jobs.value = data.results
    error.value = ''
    const requested = jobs.value.find(job => job.id === requestedJobId)
    if (!requested || requested.status === 'failed') requestedJobId = null
    if (requested?.status === 'ready') {
      requestedJobId = null
      await download(requested)
    }
  } catch (e) {
    if (!isCurrent()) return
    if ([401, 403].includes(e.statusCode)) {
      jobs.value = []
      requestedJobId = null
    }
    error.value = e.message || '导出记录读取失败，请刷新重试'
  }
  const pending = jobs.value.some(job => ['queued', 'running'].includes(job.status))
  if (isCurrent() && (requestedJobId || pending)) timer = setTimeout(load, 3000)
}

async function submit(from, to) {
  if (busy.value || !visible || !allowed.value) return
  const tag = epoch
  busy.value = true
  error.value = ''
  try {
    const data = {}
    if (from) data.date_from = from
    if (to) data.date_to = to
    const job = await request({ url, method: 'POST', data })
    if (tag !== epoch) return
    requestedJobId = job.id
    await load()
  } catch (e) {
    if (tag === epoch) error.value = e.message || '导出提交失败，请重试'
  } finally {
    if (tag === epoch) busy.value = false
  }
}

function create() {
  if (custom.value && (!start.value || !end.value || start.value > end.value)) {
    error.value = '请选择有效的开始和结束日期'
    return
  }
  submit(custom.value && start.value, custom.value && end.value)
}

watch(identity, () => {
  invalidate()
  jobs.value = []
  error.value = ''
  load()
}, { flush: 'sync' })

onShow(async () => {
  visible = true
  await auth.bootstrap()
  if (visible) load()
})

function leave() {
  visible = false
  invalidate()
}
onHide(leave)
onUnmounted(leave)
</script>

<style scoped>
.archive {max-width:760px;margin:auto;padding:16px;color:#243649;background:#f5f7fa;min-height:100vh;box-sizing:border-box}
.panel {padding:16px;background:white;border:1px solid #dfe6ed;border-radius:12px;margin-bottom:14px}
.heading {font-size:18px;font-weight:650}.note,.message {display:block;margin:8px 0;font-size:14px;line-height:1.55;color:#52687b}
.message {color:#994514}.controls,.heading-row,.job {display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.controls {margin:12px 0}.controls button,.heading-row button,.job button {margin:0}.selected {color:#154c87;background:#e4effa}
.heading-row,.job {justify-content:space-between}.job {padding:12px 0;border-bottom:1px solid #e8edf1}
</style>

import { computed, ref, watch } from 'vue'

export const countStages = [
  { key: 'work', label: '盘点任务' }, { key: 'draft', label: '草稿/待发布' },
  { key: 'review', label: '待审核' }, { key: 'posting', label: '待过账' },
]
export const countScopes = [
  { id: 'ALL', name: '全仓（所选货主）' }, { id: 'ZONE', name: '库区' },
  { id: 'LOC', name: '库位' }, { id: 'SKU', name: '商品' },
]
export function countStatus(task) {
  if (task.posting_status === 'POSTED') return '已过账'
  if (task.posting_status === 'FAILED') return '过账失败，可重试'
  if (task.review_status === 'APPROVED') return '待过账'
  if (task.review_status === 'PENDING') return '待审核'
  if (task.review_status === 'REJECTED') return '已驳回'
  return { DRAFT: '草稿', READY: '待发布', RELEASED: '待认领', IN_PROGRESS: '盘点中', COMPLETED: '已完成', CANCELLED: '已取消' }[task.status] || task.status
}
export function countError(error) {
  const text = value => Array.isArray(value) ? value.map(text).join('；') : value && typeof value === 'object' ? Object.values(value).map(text).join('；') : String(value || '')
  const detail = error?.data && typeof error.data === 'object' ? text(error.data) : ''
  return detail || error?.message || '操作失败，请重试'
}
export function sameCountQuantity(left, right) {
  const normalized = value => {
    const [whole, fraction = ''] = String(value).split('.')
    return `${whole.replace(/^0+(?=\d)/, '')}.${fraction.replace(/0+$/, '')}`
  }
  return normalized(left) === normalized(right)
}
export function hasUnsavedCounts(lines) {
  return lines.some(line => line.count_status === 'COUNTED'
    ? !sameCountQuantity(line._qty, line.qty_counted)
    : line._qty !== '')
}
export function countPayload(form) {
  if (!form.warehouse_id || !form.owner_id) throw new Error('请选择仓库和货主')
  if (form.scope === 'ZONE' && !form.zone_type) throw new Error('请选择库区')
  if (form.scope === 'LOC' && !form.location_id && !form.location_prefix?.trim()) throw new Error('请选择库位或输入库位前缀')
  if (form.scope === 'SKU' && !form.product_id) throw new Error('请选择商品')
  const max = Number(form.max_lines)
  if (!Number.isInteger(max) || max < 1 || max > 10000) throw new Error('最多生成行数须为 1–10000 的整数')
  if (!/^\d+(\.\d{1,4})?$/.test(String(form.recount_threshold)) || Number(form.recount_threshold) >= 10000000000) throw new Error('复盘阈值须为非负数，最多四位小数及十位整数')
  const payload = {
    warehouse_id: form.warehouse_id, owner_id: form.owner_id, scope: form.scope,
    blind: form.blind, exclude_zero_onhand: form.exclude_zero_onhand,
    max_lines: max, recount_threshold: String(form.recount_threshold),
    task_remark: (form.task_remark || '').trim(), batch_no: (form.batch_no || '').trim(),
  }
  if (form.subwarehouse_id) payload.subwarehouse_id = form.subwarehouse_id
  if (form.scope === 'ZONE') payload.zone_type = form.zone_type
  if (form.scope === 'SKU') payload.product_id = form.product_id
  if (form.scope === 'LOC') {
    if (form.location_id) payload.location_id = form.location_id
    else payload.location_prefix = form.location_prefix.trim()
  }
  return payload
}

// Keep responses scoped to the current account and visible page, including writes.
export function useCountSession({ api, auth, confirm = async () => true }) {
  const rows = ref([]), task = ref(null), lines = ref([]), error = ref('')
  const loading = ref(false), busy = ref(false), denied = ref(false)
  let version = 0
  const identity = () => String(auth.access || '')
  const manager = computed(() => auth.canManageCount && !denied.value)
  const invalidate = () => { version += 1; loading.value = false }
  const clear = () => { invalidate(); rows.value = []; task.value = null; lines.value = [] }
  const unwatch = watch(identity, clear, { flush: 'sync' })
  const fail = err => {
    error.value = countError(err)
    if (Number(err?.statusCode) === 403) {
      denied.value = true
      rows.value = []; task.value = null; lines.value = []
      if (auth.profile?.capabilities) auth.profile.capabilities.can_manage_count = false
    }
  }
  async function authorize() {
    await auth.loadProfile({ force: true })
    denied.value = false
  }
  async function load({ stage = 'work', id = null, refreshAuth = false } = {}) {
    const seq = ++version, account = identity()
    loading.value = true; error.value = ''
    try {
      if (refreshAuth) await authorize()
      if (seq !== version || account !== identity()) return
      if (!id && stage !== 'work' && !manager.value) throw new Error('无盘点管理权限')
      const result = id ? await Promise.all([api.countTaskDetail(id), api.countTaskLines(id)]) : await api.countTasks({ stage })
      if (seq !== version || account !== identity()) return
      if (id) {
        task.value = result[0]
        lines.value = (Array.isArray(result[1]) ? result[1] : result[1]?.results || []).map(line => ({ ...line, _qty: line.count_status === 'COUNTED' ? String(line.qty_counted) : '' }))
      } else rows.value = Array.isArray(result) ? result : result?.results || []
    } catch (err) {
      if (seq === version && account === identity()) fail(err)
    } finally {
      // A newer load owns its loading state.
      if (seq === version) loading.value = false
    }
  }
  async function act(action, payload, message) {
    if (busy.value || loading.value || denied.value) return null
    const managed = ['create', 'release', 'cancel', 'approve', 'reject', 'post'].includes(action)
    if (managed && !manager.value) { error.value = '无盘点管理权限'; return null }
    busy.value = true; error.value = ''
    const account = identity(), seq = version, id = task.value?.id
    try {
      if (message && !await confirm(message)) return null
      if (account !== identity() || seq !== version) return null
      if (managed && !manager.value) { error.value = '无盘点管理权限'; return null }
      const methods = { create: 'createCountTask', release: 'releaseCountTask', cancel: 'cancelCountTask', approve: 'approveCountTask', reject: 'rejectCountTask', post: 'postCountTask', claim: 'claimCountTask', submit: 'submitCountTask', record: 'recordCount' }
      const result = action === 'create' ? await api[methods[action]](payload) : await api[methods[action]](id, payload)
      if (account !== identity() || seq !== version) return null
      return result
    } catch (err) {
      if (account === identity() && seq === version) fail(err)
      return null
    } finally { busy.value = false }
  }
  return { rows, task, lines, error, loading, busy, denied, manager, load, act, authorize, invalidate, dispose: () => { clear(); unwatch() } }
}

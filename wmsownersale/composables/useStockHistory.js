import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { onHide, onShow, onUnload } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { ownerAccess } from '@/utils/ownerAccess'
import { api } from '@/utils/request'
import { blankHistoryFilters, historyQuery } from '@/utils/stockHistory'

export function useStockHistory() {
  const auth = useAuth()
  const access = computed(() => ownerAccess({roles: auth.roles, capabilities: auth.capabilities}))
  const identity = computed(() => JSON.stringify([auth.user?.id, auth.user?.owner_id, auth.roles, auth.capabilities]))
  const denied = ref(false)
  const allowed = computed(() => access.value.canViewStockHistory && !denied.value)
  const canExport = computed(() => allowed.value && access.value.canExportStockHistory)
  const form = reactive(blankHistoryFilters())
  const applied = ref(blankHistoryFilters())
  const view = ref('products')
  const eventId = ref('')
  const summary = ref(null)
  const detail = ref(null)
  const rows = ref([])
  const error = ref('')
  const loading = ref(false)
  const nextPage = ref(null)
  const count = ref(0)
  const snapshot = ref(null)
  let generation = 0
  let active = false

  function clear() {
    summary.value = null; detail.value = null; rows.value = []; nextPage.value = null
    count.value = 0; snapshot.value = null
  }
  function revoke() {
    generation++; loading.value = false; denied.value = true; clear()
    error.value = '出入库历史访问权限已失效'
  }
  function stop() { active = false; generation++; loading.value = false }

  async function load(more = false) {
    if (!active || loading.value || (more && !nextPage.value)) return
    if (!allowed.value) { revoke(); return }
    const tag = ++generation
    const signature = identity.value
    const current = () => active && tag === generation && signature === identity.value && allowed.value
    const params = { ...historyQuery(applied.value), page_size: 30, page: more ? nextPage.value : 1 }
    error.value = ''; loading.value = true
    try {
      let header = summary.value
      if (!more && !eventId.value) {
        header = await api.stockStatistics('summary', {...params})
        if (!current()) return
      }
      params.snapshot = eventId.value ? snapshot.value ?? applied.value.snapshot : header?.snapshot
      const section = eventId.value ? `events/${encodeURIComponent(eventId.value)}` : applied.value.grain !== 'all' ? 'periods' : view.value
      const data = await api.stockStatistics(section, params)
      if (!current()) return
      if (!Array.isArray(data?.results)) throw new Error('历史数据不完整，请重试')
      summary.value = header
      if (eventId.value) detail.value = { ...data, receipts: more ? [...(detail.value?.receipts || []), ...(data.receipts || [])].filter((r,i,a) => a.findIndex(x => x.id === r.id) === i) : data.receipts }
      rows.value = more ? [...rows.value, ...data.results] : data.results
      snapshot.value = data.snapshot; count.value = data.count; nextPage.value = data.next_page
    } catch (e) {
      if (!current()) return
      if ([401, 403, 409].includes(Number(e?.statusCode || e?.status))) { revoke(); return }
      error.value = rows.value.length ? (more ? '加载更多失败，请重试' : '刷新失败，当前为上次数据') : e?.message || '出入库记录加载失败，请重试'
    } finally { if (tag === generation) loading.value = false }
  }

  function loadFilters(filters) {
    generation++
    loading.value = false
    applied.value = filters
    clear()
    load()
  }

  function apply(changes = {}) {
    const next = { ...form, ...changes }
    if (next.date_from && next.date_to && next.date_from > next.date_to) {
      error.value = '开始日期不能晚于结束日期'; return
    }
    Object.assign(form, changes)
    if (form.date_from || form.date_to) delete form.undated
    loadFilters({ ...form })
  }
  function selectView(value) {
    const grain = value === 'day' ? 'day' : 'all'
    if (applied.value.grain === grain && (grain === 'day' || view.value === value)) return
    if (grain === 'all') view.value = value
    form.grain = grain
    loadFilters({ ...applied.value, grain })
  }
  function configure(options = {}) {
    for (const key of Object.keys(form)) if (options[key] != null) form[key] = options[key]
    if (options.undated) form.undated = options.undated
    if (options.snapshot != null) form.snapshot = options.snapshot
    applied.value = { ...form }
    if (['products', 'documents', 'events'].includes(options.view)) view.value = options.view
    eventId.value = /^\d+$/.test(options.event_id || '') ? `ledger-${options.event_id}` : options.event_id || ''
  }
  function refresh() {
    delete form.snapshot; delete applied.value.snapshot
    snapshot.value = null
    return load()
  }
  onShow(async () => {
    active = true
    const tag = ++generation
    try { await auth.bootstrap() } catch { /* Requests expose the actionable error. */ }
    if (!active || tag !== generation) return
    if (!allowed.value) { revoke(); return }
    if (!summary.value && !detail.value) load()
  })
  onHide(stop); onUnload(stop); onUnmounted(stop)
  watch(identity, () => {
    generation++; loading.value = false; denied.value = false; clear()
    delete form.snapshot; delete applied.value.snapshot
    error.value = ''
    if (active) load()
  }, {flush: 'sync'})
  return { form, applied, view, eventId, summary, detail, rows, error, loading,
    count, nextPage, snapshot, identity, allowed, canExport, revoke, configure, apply, selectView, load, refresh }
}

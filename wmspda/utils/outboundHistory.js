import { computed, reactive, ref, unref, watch } from 'vue'

export function localDate(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
export function defaultPeriod(stats = false) {
  const end = new Date(), start = new Date(end)
  if (!stats) start.setDate(start.getDate() - 29)
  return { start_date: localDate(start), end_date: localDate(end) }
}
export function queryString(params) {
  return Object.entries(params).filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&')
}
export function quantity(value) { return value == null ? '0' : String(value).replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1') }
export function errorText(error) {
  const data = error?.data
  if (data && typeof data === 'object') return Object.values(data).flat().join('；')
  return error?.message || '查询失败，请重试'
}

// Each response belongs to one account and an immutable query snapshot.
export function useOutboundHistory({ api, auth, assisted = false, statsMode = false, detailId = null }) {
  const defaults = () => ({ ...defaultPeriod(statsMode), search: '', owner_id: '',
    ...(assisted ? { operator_id: '', status: '' } : { warehouse_id: '', processing_mode: '', outbound_type: '', submit_status: '', approval_status: '', is_closed: '' }),
  })
  const filters = reactive(defaults()), applied = ref(null)
  const rows = ref([]), stats = ref(null), order = ref(null), options = ref({})
  const count = ref(0), page = ref(0), hasMore = ref(false), loading = ref(false)
  const error = ref(''), authorized = ref(false)
  let sequence = 0, alive = true, retryArgs = {}
  const identity = () => `${auth.access || ''}:${auth.user?.id || ''}`
  function clear() {
    ++sequence
    rows.value = []; stats.value = null; order.value = null; options.value = {}
    applied.value = null; count.value = 0; page.value = 0; hasMore.value = false
    authorized.value = false; loading.value = false; error.value = ''
  }
  function accountChanged() { clear(); Object.assign(filters, defaults()); retryArgs = {} }
  const stopAccess = watch(() => auth.access, accountChanged, { flush: 'sync' })
  const stopUser = watch(() => auth.user?.id, (next, prev) => {
    if (prev != null && next !== prev) accountChanged()
  }, { flush: 'sync' })
  function failed(e) {
    error.value = errorText(e)
    if ([401, 403].includes(Number(e?.statusCode || e?.status || e?.code))) {
      clear(); error.value = errorText(e)
      if (assisted) auth.invalidateAssistedCapability()
    }
  }
  function applyRoute(query = {}) {
    for (const key of Object.keys(filters)) if (query[key] != null) filters[key] = query[key]
  }
  function validate(params) {
    const validDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value
    if (!validDate(params.start_date) || !validDate(params.end_date)) throw new Error('请选择有效日期')
    if (params.start_date > params.end_date) throw new Error('开始日期不能晚于结束日期')
    if (statsMode && (Date.parse(params.end_date) - Date.parse(params.start_date)) / 86400000 >= 366) throw new Error('单次统计范围不能超过366天')
  }
  async function load(args = {}) {
    const { more = false, params = null } = args
    if (!alive || !authorized.value || (more && (loading.value || !hasMore.value))) return
    const snapshot = { ...(params || (more ? applied.value : filters)) }
    retryArgs = { more, params: snapshot }
    const current = ++sequence, account = identity(), nextPage = more ? page.value + 1 : 1
    const currentRequest = () => alive && sequence === current && identity() === account
    loading.value = true; error.value = ''
    if (!more) {
      rows.value = []; stats.value = null; order.value = null
      count.value = 0; hasMore.value = false; applied.value = null
    }
    try {
      if (detailId !== null) {
        if (!Number.isInteger(Number(unref(detailId))) || Number(unref(detailId)) <= 0) throw new Error('订单参数不正确')
        const result = await api.outboundHistoryDetail(unref(detailId))
        if (currentRequest()) order.value = result
      } else {
        validate(snapshot)
        const fetcher = statsMode
          ? (assisted ? api.assistedOutboundStats : api.outboundHistoryStats)
          : (assisted ? api.assistedOutboundHistory : api.outboundHistory)
        const result = await fetcher({ ...snapshot, ...(statsMode ? { top_n: 10 } : { page: nextPage, page_size: 20 }) })
        if (!currentRequest()) return
        applied.value = { ...snapshot }
        if (statsMode) stats.value = result
        else {
          rows.value = more ? [...rows.value, ...(result.results || [])] : (result.results || [])
          count.value = Number(result.count || 0); hasMore.value = Boolean(result.next); page.value = nextPage
        }
      }
    } catch (e) { if (currentRequest()) failed(e) }
    finally { if (currentRequest()) loading.value = false }
  }
  async function initialize() {
    clear()
    const current = sequence
    loading.value = true
    try {
      await auth.loadProfile({ force: true })
      if (!alive || sequence !== current) return
      if (!auth.profileLoaded) throw new Error('无法读取账号权限，请重试')
      if (assisted && !auth.canProcessAssistedOutbound) throw { statusCode: 403, message: '当前账号无代货主出库权限' }
      const account = identity()
      const result = detailId === null ? await (assisted ? api.assistedOutboundHistoryOptions() : api.outboundHistoryOptions()) : {}
      if (!alive || sequence !== current || identity() !== account) return
      options.value = result; authorized.value = true
      await load()
    } catch (e) { if (alive && current === sequence) failed(e) }
    finally { if (alive && current === sequence) loading.value = false }
  }
  function reset() { Object.assign(filters, defaults()); return load() }
  function retry() { return authorized.value ? load(retryArgs) : initialize() }
  function suspend() { clear() }
  function dispose() { alive = false; clear(); stopAccess(); stopUser() }
  const dirty = computed(() => Boolean(applied.value && Object.keys(filters).some(key => String(filters[key] ?? '') !== String(applied.value[key] ?? ''))))
  const periodLabel = computed(() => applied.value ? `${applied.value.start_date} 至 ${applied.value.end_date}` : '')
  return { filters, applied, rows, stats, order, options, count, page, hasMore, loading, error, authorized, dirty, periodLabel,
    applyRoute, initialize, load, reset, retry, suspend, dispose }
}

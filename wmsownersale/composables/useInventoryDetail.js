import { reactive, ref, watch } from 'vue'
import { api } from '@/utils/request'
import { useInventoryAccess } from '@/composables/useInventoryAccess'

export function useInventoryDetail() {
  const access = useInventoryAccess()
  const id = ref(0)
  const tab = ref('detail')
  const header = ref(null)
  const denied = ref(false)
  let active = false
  const makeState = () => ({ rows: [], data: null, loading: false, error: '', next: null, generation: 0, filters: {} })
  const sections = reactive({ detail: makeState(), transactions: makeState() })

  function invalidate() {
    Object.values(sections).forEach((s) => { s.generation++; s.loading = false })
  }
  function stop() { active = false; invalidate() }
  function clearAccess(message) {
    denied.value = true
    header.value = null
    invalidate()
    Object.values(sections).forEach((s) => {
      s.rows = []; s.data = null; s.next = null; s.error = message
    })
  }
  async function load(kind = tab.value, more = false) {
    const state = sections[kind]
    if (!access.canView.value) { clearAccess('登录状态或库存访问权限已失效'); return }
    if (!active || !id.value || denied.value || state.loading || (more && !state.next)) return
    const tag = ++state.generation
    const orderId = id.value
    state.loading = true
    state.error = ''
    const params = { ...state.filters, page_size: 50 }
    if (more) params[kind === 'detail' ? 'page' : 'cursor'] = state.next
    try {
      const data = await (kind === 'detail' ? api.inventoryDetail : api.inventoryTransactions)(orderId, params)
      if (!active || tag !== state.generation || orderId !== id.value) return
      if (!data || data.summary_id !== orderId || !Array.isArray(data.results)) throw new Error('数据不完整')
      state.rows = more ? [...state.rows, ...data.results] : data.results
      state.data = data
      state.next = kind === 'detail' ? data.next_page : data.next_cursor
      if (kind === 'detail') header.value = data
    } catch (err) {
      if (!active || tag !== state.generation || orderId !== id.value) return
      const status = Number(err?.statusCode || err?.status || 0)
      if ([401, 403].includes(status)) { access.revoke(); clearAccess('登录状态或库存访问权限已失效') }
      else if (status === 409) clearAccess('登录状态或库存访问权限已失效')
      else if (status === 404) clearAccess('库存明细暂不可用')
      else state.error = state.data ? (more ? '加载失败，请重试' : '更新失败，当前为上次数据') : '库存明细暂不可用，请重试'
    } finally {
      if (tag === state.generation) state.loading = false
    }
  }
  function show() {
    active = true
    load('detail')
    if (sections.transactions.data || tab.value === 'transactions') load('transactions')
  }
  function setId(value) {
    stop()
    id.value = Number(value) || 0
    denied.value = false
    header.value = null
    Object.values(sections).forEach((s) => Object.assign(s, makeState(), { generation: s.generation + 1 }))
  }
  function select(kind) {
    tab.value = kind
    if (!sections[kind].data) load(kind)
  }
  function apply(kind, filters) {
    const s = sections[kind]
    s.generation++; s.loading = false; s.data = null; s.rows = []; s.next = null
    s.filters = Object.fromEntries(Object.entries(filters).filter(([, v]) => v !== '' && v != null))
    load(kind)
  }
  watch(access.identity, () => {
    clearAccess('登录状态或库存访问权限已失效')
    if (access.canView.value) { denied.value = false; if (active) show() }
  }, {flush: 'sync'})
  return { id, tab, header, denied, sections, setId, show, stop, select, apply, load }
}

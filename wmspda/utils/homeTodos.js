import { computed, ref } from 'vue'

export const todoKinds = {
  outbound_review: { label: '出库待审核', unit: '个订单', path: '/pages/outbound/review/index' },
  receive: { label: '待收货', unit: '个任务', path: '/pages/inbound/receive_task_list' },
  putaway: { label: '待上架', unit: '个任务', path: '/pages/inbound/putaway_task_list' },
  pick: { label: '待拣货', unit: '个任务', path: '/pages/picking/task_list' },
  review: { label: '待复核／过账', unit: '个任务', path: '/pages/review/pick_task_list' },
  replenish: { label: '待补货', unit: '个任务', path: '/pages/inventory/replenish/index?pending=1' },
}

export function useHomeTodos({ fetchTodos }) {
  const items = ref([]), loading = ref(false), error = ref(''), loaded = ref(false)
  const popup = ref(false)
  const pending = computed(() => items.value.filter(item => item.count > 0))
  let visible = false, sequence = 0, timer = null, scope = '', previous = null

  function clear() {
    items.value = []; loaded.value = false; popup.value = false
    previous = null; scope = ''; error.value = ''
  }
  async function refresh() {
    if (!visible || loading.value) return
    const tag = ++sequence
    loading.value = true
    error.value = ''
    try {
      const data = await fetchTodos()
      if (!visible || tag !== sequence) return
      if (!data?.scope || !Array.isArray(data.items) || data.items.some(item =>
        !todoKinds[item.key] || !Number.isSafeInteger(item.count) || item.count < 0
      )) throw new Error('待办响应不完整')
      if (scope !== data.scope) clear()
      scope = data.scope
      items.value = data.items.map(item => ({ ...todoKinds[item.key], ...item }))
      const increased = pending.value.some(item => item.count > (previous?.[item.key] ?? 0))
      previous = Object.fromEntries(data.items.map(item => [item.key, item.count]))
      loaded.value = true
      if (increased) popup.value = true
      if (!pending.value.length) popup.value = false
    } catch (e) {
      if (!visible || tag !== sequence) return
      // Failed refreshes never leave an actionable stale popup or permission data.
      items.value = []; popup.value = false; loaded.value = false
      if ([401, 403, 409].includes(Number(e?.statusCode || e?.code))) clear()
      error.value = '更新失败，请重试'
    } finally {
      if (tag === sequence) loading.value = false
    }
  }
  function hide() {
    visible = false; sequence++; loading.value = false; popup.value = false
    clearInterval(timer); timer = null
  }
  function show() {
    if (visible) return
    visible = true
    refresh()
    timer = setInterval(refresh, 60000)
  }
  function reset() {
    sequence++; loading.value = false; clear()
    if (visible) refresh()
  }
  return { items, pending, loading, loaded, error, popup, refresh, show, hide, reset }
}

import { onUnmounted, ref, watch } from 'vue'
import { api } from '@/utils/request'

export function useOwnerWorkbench(active, identity) {
  const data = ref(null)
  const loading = ref(false)
  const error = ref('')
  let stopped = false
  let alive = true
  let generation = 0
  let inFlight = false
  let queued = false
  let timer = null
  const eligible = () => alive && active.value && !stopped && (typeof document === 'undefined' || !document.hidden)
  const clearTimer = () => { clearTimeout(timer); timer = null }
  async function refresh() {
    if (!eligible()) return
    clearTimer()
    if (inFlight) { queued = true; return }
    const tag = generation
    inFlight = true; queued = false; loading.value = true
    try {
      const result = await api.ownerWorkbench()
      if (!eligible() || tag !== generation) return
      if (!Array.isArray(result?.categories)) throw new Error('待办数据不完整')
      data.value = result; error.value = ''
    } catch (e) {
      if (!eligible() || tag !== generation) return
      const status = Number(e?.statusCode || e?.status)
      if ([401, 403, 409].includes(status)) {
        data.value = null; stopped = true; error.value = '登录状态或待办权限已失效'
      } else {
        if (status === 404) data.value = null
        error.value = data.value ? '更新失败，当前为上次数据' : '待办暂不可用，请稍后重试'
      }
    } finally {
      inFlight = false
      if (alive) loading.value = false
      if (eligible()) {
        if (queued) { queued = false; refresh() }
        else timer = setTimeout(refresh, 30000)
      }
    }
  }
  function changed() {
    generation++; queued = false; clearTimer(); loading.value = false
    if (eligible()) refresh()
  }
  const unwatch = watch([active, identity], (values, previous) => {
    if (!previous || values[1] !== previous[1]) {
      data.value = null; error.value = ''; stopped = false
    }
    changed()
  }, { immediate: true })
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', changed)
  onUnmounted(() => {
    alive = false; generation++; clearTimer(); unwatch()
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', changed)
  })
  return { data, loading, error, refresh }
}

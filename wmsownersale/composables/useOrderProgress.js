import { onUnmounted, ref, watch } from 'vue'
import { api } from '@/utils/request'

export function useOrderProgress(orderId, active) {
  const progress = ref(null)
  const loading = ref(false)
  const error = ref('')
  const stopped = ref(false)
  let timer = null
  let generation = 0
  let inFlight = false
  let queued = false
  let alive = true

  const visible = () => active.value && (typeof document === 'undefined' || !document.hidden)
  const eligible = () => alive && orderId.value > 0 && visible() && !stopped.value
  const clearTimer = () => { clearTimeout(timer); timer = null }

  async function refresh() {
    if (!eligible()) return
    clearTimer()
    if (inFlight) { queued = true; return }
    inFlight = true
    loading.value = true
    queued = false
    const tag = generation
    const id = orderId.value
    try {
      const result = await api.orderProgress(id)
      if (!alive || tag !== generation || !eligible()) return
      if (!result || result.order_id !== id) throw new Error('进度数据不完整')
      progress.value = result
      error.value = ''
    } catch (err) {
      if (!alive || tag !== generation || !eligible()) return
      const status = Number(err?.statusCode || err?.status || 0)
      if ([401, 403, 404, 409].includes(status)) {
        progress.value = null
        stopped.value = true
        error.value = status === 404 ? '进度暂不可用' : '登录状态或订单访问权限已失效'
      } else {
        error.value = progress.value ? '更新失败，当前为上次数据' : '进度暂不可用，请稍后重试'
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

  function visibilityChanged() {
    generation++
    clearTimer()
    queued = false
    loading.value = false
    if (eligible()) refresh()
  }

  const unwatch = watch([orderId, active], ([id], previous) => {
    if (!previous || id !== previous[0]) {
      progress.value = null
      error.value = ''
      stopped.value = false
    }
    visibilityChanged()
  }, { immediate: true })

  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', visibilityChanged)
  onUnmounted(() => {
    alive = false
    generation++
    clearTimer()
    unwatch()
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', visibilityChanged)
  })
  return { progress, loading, error, stopped, refresh }
}

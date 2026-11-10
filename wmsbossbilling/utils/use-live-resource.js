import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { createLiveRefresh } from './live-refresh'
import { getStoredToken } from './request'
import { useRefreshPreferences } from '@/store/refreshPreferences'

export function useLiveResource(load, { key, enabled = () => true, hold = () => false, compare }) {
  const preferences = useRefreshPreferences()
  let running = false
  let needsLoad = true
  const data = ref(null)
  const pending = ref(null)
  const error = ref(null)
  const loading = ref(false)
  const updatedAt = ref('')
  let visible = true
  let appVisible = true
  let online = true
  let disposed = false
  let revision = 0
  let acceptNext = false
  const canRun = () => !disposed && visible && appVisible && online && enabled() && !!getStoredToken()
    && (typeof document === 'undefined' || !document.hidden)
  const controller = createLiveRefresh(async () => {
    const version = revision
    const fingerprint = key()
    if (!canRun()) return
    loading.value = !data.value
    try {
      const result = await load()
      if (version !== revision || fingerprint !== key() || !canRun()) return
      error.value = null
      updatedAt.value = new Date().toLocaleTimeString()
      const dateChanged = data.value?.scope?.date_to && result?.scope?.date_to !== data.value.scope.date_to
      if (data.value && hold() && !acceptNext) {
        // Ignore response metadata, whose query timestamp changes every poll.
        const content = value => {
          const { meta, scope, ...records } = value || {}
          return JSON.stringify(records)
        }
        const comparable = compare || content
        if (dateChanged || comparable(result) !== comparable(data.value)) pending.value = result
        else pending.value = null
      } else {
        data.value = result
        pending.value = null
      }
      acceptNext = false
    } catch (err) {
      if (version !== revision || fingerprint !== key() || !canRun()) return
      error.value = err
      if ([401, 403, 404].includes(err.statusCode) || err.kind === 'FORBIDDEN') {
        data.value = null
        pending.value = null
      }
      throw err
    } finally {
      if (version === revision) loading.value = false
    }
  }, { automatic: preferences.automatic, interval: preferences.minutes * 60000 })
  function sync() {
    if (!canRun()) {
      if (running) { revision++; loading.value = false }
      running = false
      controller.pause()
      return
    }
    if (!running) {
      running = true
      const immediate = needsLoad || preferences.automatic
      needsLoad = false
      controller.resume({ refresh: immediate })
    } else if (needsLoad) {
      needsLoad = false
      controller.refresh()
    }
  }
  watch(() => [preferences.automatic, preferences.minutes], ([automatic, minutes], [previous]) => {
    controller.configure({ automatic, interval: minutes * 60000 })
    if (automatic && !previous && canRun()) controller.refresh()
  })
  function reset() {
    revision++
    needsLoad = true
    data.value = null; pending.value = null; error.value = null; updatedAt.value = ''; loading.value = false
    sync()
  }
  function apply() { if (pending.value) { data.value = pending.value; pending.value = null } }
  function refresh() { apply(); acceptNext = true; return controller.refresh() }
  function pageShow() { visible = true; sync() }
  function pageHide() { visible = false; sync() }
  function appShow() { appVisible = true; sync() }
  function appHide() { appVisible = false; sync() }
  function network(event) { online = event.isConnected; sync() }
  function dispose() {
    if (disposed) return
    disposed = true; revision++; controller.dispose()
    uni.offNetworkStatusChange?.(network)
    uni.$off?.('boss:foreground', appShow); uni.$off?.('boss:background', appHide)
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', sync)
  }
  watch([key, enabled, () => preferences.userId], reset)
  onMounted(() => {
    uni.onNetworkStatusChange?.(network)
    uni.$on?.('boss:foreground', appShow); uni.$on?.('boss:background', appHide)
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', sync)
    sync()
  })
  onShow(pageShow); onHide(pageHide); onUnload(dispose); onBeforeUnmount(dispose)
  return { data, pending, error, loading, updatedAt, refresh, apply, reset }
}

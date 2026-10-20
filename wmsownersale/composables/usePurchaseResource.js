import { ref, watch, onMounted, onUnmounted } from 'vue'
import { onShow, onHide, onUnload } from '@dcloudio/uni-app'

// Serialize physical requests and discard results after identity/query/visibility changes.
export function usePurchaseResource(loader, key, identity) {
  const data = ref(null), error = ref(''), loading = ref(false), denied = ref(false)
  const updatedAt = ref('')
  let alive = true, active = false, running = false, epoch = 0, queued = null
  const visible = () => alive && active && !denied.value &&
    (typeof document === 'undefined' || !document.hidden)
  async function run(options = { page: 1 }) {
    if (!visible() || !key()) return
    if (running) { queued = options; return }
    const tag = epoch, identityTag = identity(), keyTag = key()
    running = true; loading.value = true; error.value = ''
    try {
      const result = await loader(options)
      if (!visible() || tag !== epoch || identityTag !== identity() || keyTag !== key()) return
      data.value = options.page > 1 && data.value?.results
        ? { ...result, results: [...data.value.results, ...result.results] } : result
      updatedAt.value = new Date().toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false})
    } catch (e) {
      if (!visible() || tag !== epoch || identityTag !== identity() || keyTag !== key()) return
      const status = Number(e?.statusCode || e?.status || e?.code)
      if ([401, 403].includes(status) || (status === 404 && data.value)) {
        data.value = null; updatedAt.value = ''; denied.value = true
        error.value = status === 404
          ? '该记录已不可访问，请返回并重新选择'
          : '访问权限已失效，请重新登录或联系管理员'
      } else {
        error.value = data.value ? '更新失败，当前为上次数据' :
          ([404, 405].includes(status) ? '快捷下单暂不可用' :
            e?.data?.detail || e?.message || '加载失败，请重试')
      }
    } finally {
      running = false
      if (alive) loading.value = false
      if (queued && visible()) { const next = queued; queued = null; run(next) }
    }
  }
  function refresh() { epoch++; return run() }
  function show() { if (!active) { active = true; refresh() } }
  function hide() { active = false; epoch++; queued = null }
  function reset() { epoch++; queued = null; data.value = null; updatedAt.value = ''; error.value = ''; denied.value = false }
  function visibility() { epoch++; if (visible()) run() }
  function dispose() {
    alive = false; hide()
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', visibility)
  }
  watch(key, () => { reset(); run() })
  watch(identity, () => { reset(); run() })
  onMounted(show); onShow(show); onHide(hide); onUnload(dispose); onUnmounted(dispose)
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', visibility)
  return { data, error, loading, denied, updatedAt, refresh, more: page => run({ page }) }
}

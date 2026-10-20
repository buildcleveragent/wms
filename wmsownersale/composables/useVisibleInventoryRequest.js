import { ref, watch, onUnmounted } from 'vue'
import { onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { useInventoryAccess } from '@/composables/useInventoryAccess'

// One physical request per page. Invalidated requests finish before the latest query starts.
export function useVisibleInventoryRequest(loader, key, identity, blocked = ref(false)) {
  const access = useInventoryAccess()
  const data = ref(null)
  const error = ref('')
  const loading = ref(false)
  const denied = ref(false)
  let active = false, alive = true, inflight = false, generation = 0
  let pending = null, failed = { page: 1 }
  const visible = () => alive && active && !denied.value && (typeof document === 'undefined' || !document.hidden)
  async function run(options = { page: 1 }) {
    if (!access.canView.value) {
      reset(); denied.value = true; error.value = '库存预警访问权限已失效'; return
    }
    if (!visible()) return
    if (blocked.value) { pending = options; return }
    if (inflight) { pending = options; return }
    const tag = generation
    inflight = true; loading.value = true; error.value = ''; pending = null
    try {
      const result = await loader(options)
      if (!visible() || tag !== generation) return
      if (!result || typeof result !== 'object') throw new Error('响应不完整')
      if (options.page > 1 && data.value?.results) {
        result.results = Array.from(new Map([...data.value.results, ...result.results].map(r => [r.product?.id || r.id, r])).values())
      }
      data.value = result
    } catch (e) {
      if (!visible() || tag !== generation) return
      failed = options
      const status = Number(e?.statusCode || e?.status)
      if ([401, 403].includes(status)) {
        access.revoke()
        data.value = null; denied.value = true; error.value = '库存预警访问权限已失效'
      } else error.value = data.value ? '更新失败，当前为上次数据' : '库存预警暂不可用，请重试'
    } finally {
      inflight = false
      if (alive) loading.value = false
      if (pending && visible()) { const next = pending; pending = null; run(next) }
      uni.stopPullDownRefresh?.()
    }
  }
  function refresh() { generation++; return run({ page: 1 }) }
  function reset() { generation++; data.value = null; error.value = ''; pending = null }
  function hide() { active = false; generation++; pending = null }
  function dispose() { alive = false; hide(); if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', visibility) }
  function visibility() { generation++; if (visible()) run({ page: 1 }) }
  watch(key, () => { reset(); run({ page: 1 }) })
  watch(identity, () => { reset(); denied.value = false; run({ page: 1 }) })
  watch(access.identity, () => { reset(); denied.value = false; run({page: 1}) }, {flush: 'sync'})
  watch(blocked, value => {
    if (!value && pending && visible()) { const next = pending; pending = null; run(next) }
  })
  onShow(() => { active = true; refresh() })
  onHide(hide); onUnload(dispose); onUnmounted(dispose)
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', visibility)
  return { data, error, loading, denied, refresh, retry: () => run(failed), more: page => { if (!inflight) run({ page }) } }
}

import { ref, watch, onUnmounted } from 'vue'
import { onHide, onShow, onUnload } from '@dcloudio/uni-app'
import { downloadAuthenticatedFile } from '@/utils/request'

export function useOwnerExport(context, { allowed = () => true, onDenied = () => {} } = {}) {
  const exporting = ref(false)
  const exportError = ref('')
  let generation = 0
  let active = true
  const invalidate = () => { generation++; exportError.value = '' }
  const stop = () => { active = false; invalidate() }
  watch(context, invalidate)
  watch(allowed, invalidate, {flush: 'sync'})
  onHide(stop)
  onShow(() => { active = true })
  onUnload(stop)
  onUnmounted(stop)

  async function exportExcel(path, filters, type) {
    if (exporting.value || !active || !allowed()) return
    const tag = generation
    const signature = JSON.stringify(context())
    const isCurrent = () => active && allowed() && tag === generation && signature === JSON.stringify(context())
    const query = Object.entries(filters).filter(([, v]) => v !== '' && v != null)
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&')
    exporting.value = true
    exportError.value = ''
    try {
      const stamp = new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'}).format(new Date()).replace(/[ :]/g, '-')
      await downloadAuthenticatedFile(`${path}${query ? '?' + query : ''}`, `${type}-${stamp}.xlsx`, {isCurrent, excel: true})
    } catch (e) {
      if (!isCurrent() || e?.statusCode === 409) return
      if ([401, 403].includes(Number(e?.statusCode))) onDenied()
      exportError.value = [404, 405].includes(Number(e?.statusCode))
        ? '导出暂不可用'
        : [401, 403].includes(Number(e?.statusCode)) ? '登录状态或导出权限已失效'
          : e?.message || '导出失败，请重试'
    } finally { exporting.value = false }
  }
  return { exporting, exportError, exportExcel }
}

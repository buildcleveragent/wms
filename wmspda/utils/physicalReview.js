import { computed, ref, unref } from 'vue'
export { formatQuantity } from '../../frontend/shared/quantity.js'

const message = e => Array.isArray(e?.data) ? e.data.join('；') : e?.data?.detail || e?.message || '加载失败，请重试'
const status = e => Number(e?.statusCode || e?.status)

export function usePhysicalReview({ api, auth, confirm, notify, print = null, id = null }) {
  const rows = ref([]), task = ref(null), lines = ref([]), order = ref(null), totals = ref([])
  const ready = ref(false)
  const loading = ref(false), busy = ref(false), denied = ref(false), error = ref('')
  const page = ref(1), hasMore = ref(false), stage = ref('pending'), search = ref('')
  const canSubmit = computed(() => ready.value && order.value && lines.value.length > 0 && !loading.value && !busy.value && !denied.value &&
    (task.value?.can_review === true || task.value?.can_retry_post === true))
  let alive = true, sequence = 0

  async function fail(e) {
    if (!alive) return
    ready.value = false
    error.value = message(e)
    if ([401, 403].includes(status(e))) {
      denied.value = true
      rows.value = []; task.value = null; lines.value = []; order.value = null; totals.value = []; hasMore.value = false
      auth.clearProfile()
      await auth.loadProfile({ force: true }).catch(() => {})
    } else if (status(e) === 404 && id !== null) {
      task.value = null; lines.value = []; order.value = null; totals.value = []
    }
  }

  async function load({ more = false } = {}) {
    if (!alive || busy.value || (more && (loading.value || !hasMore.value))) return
    const current = ++sequence
    loading.value = true; error.value = ''
    try {
      if (id === null) {
        const next = more ? page.value + 1 : 1
        const data = await api.reviewPickTasks({ page: next, stage: stage.value, search: search.value.trim() })
        if (!alive || current !== sequence) return
        const items = Array.isArray(data) ? data : data.results
        rows.value = more ? [...rows.value, ...items] : items
        page.value = next; hasMore.value = Boolean(data.next)
      } else {
        if (!Number.isInteger(unref(id)) || unref(id) <= 0) throw new Error('任务参数不正确')
        const data = await api.pickReviewContext(unref(id))
        if (!alive || current !== sequence) return
        const numeric = value => value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value))
        if (!data?.task || data.task.id !== unref(id) || !Array.isArray(data.lines) ||
            !Array.isArray(data.quantity_totals) || !('order' in data) ||
            data.lines.some(line => !line.product_id || !line.order_line_id || !line.base_uom_id ||
              !line.base_uom_name || !numeric(line.order_qty) || !numeric(line.qty_plan) || !numeric(line.qty_done) ||
              !Array.isArray(line.picking_details)) ||
            data.quantity_totals.some(row => !row.base_uom_id || !row.base_uom_name ||
              !numeric(row.order_qty) || !numeric(row.qty_plan) || !numeric(row.qty_done)) ||
            (data.lines.length > 0 && (!data.order?.id || !data.order.order_no || !data.quantity_totals.length))) {
          throw new Error('复核详情数据不完整，请刷新或联系管理员核对页面与接口版本')
        }
        task.value = data.task; lines.value = data.lines; order.value = data.order; totals.value = data.quantity_totals
        ready.value = !data.content_error && Boolean(data.order?.id && data.lines.length)
        if (!ready.value) error.value = data.content_error || '没有完整的有效拣货明细，不能复核'
      }
      denied.value = false
    } catch (e) {
      if (current === sequence) await fail(e)
    } finally {
      if (alive && current === sequence) loading.value = false
    }
  }

  async function submit() {
    if (!canSubmit.value) return
    const currentTask = task.value
    busy.value = true; error.value = ''
    let refresh = false, actionError = '', preparedWindow = null
    try {
      if (!await confirm(currentTask.can_retry_post ? '确认重试库存过账？' : '确认商品、数量及库位核对无误，并复核过账？', () => { preparedWindow = print?.prepare() }) || !alive) return
      const result = await api.postPickTask(currentTask.id)
      if (!alive) return
      if (result?.posted !== true) throw new Error('未确认库存过账成功，请刷新查看任务状态')
      task.value = { ...currentTask, can_review: false, can_retry_post: false, posting_status: 'POSTED', can_print_delivery: true, blocked_reason: '任务已完成复核并过账。' }
      refresh = true
      notify(result.idempotent ? '任务已处理，已刷新结果' : '复核通过，已过账')
      if (print) {
        try { await print.open(currentTask.id, preparedWindow) }
        catch { print.close(preparedWindow); notify('出库已成功，打印失败，请补打送货单') }
        preparedWindow = null
      }
    } catch (e) {
      await fail(e)
      actionError = message(e)
      refresh = !denied.value && [400, 409, 500].includes(status(e))
    } finally {
      print?.close(preparedWindow)
      busy.value = false
      if (alive && refresh) await load()
      if (alive && actionError) error.value = actionError
    }
  }
  function dispose() { alive = false; sequence++ }
  return { rows, task, lines, order, totals, ready, loading, busy, denied, error, page, hasMore, stage, search, canSubmit, load, submit, dispose }
}

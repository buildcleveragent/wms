import { computed, reactive, ref, unref } from 'vue'
export { formatQuantity } from '../../frontend/shared/quantity.js'
export { amountSummaryLabel } from '../../frontend/shared/amountSummary.js'

export const reviewStages = [
  { key: 'pending', label: '待确认' }, { key: 'waiting', label: '等待补货' },
  { key: 'approved', label: '已确认' }, { key: 'rejected', label: '已退回' },
]
export const reviewStatus = (status) => ({
  OWNER_APPROVED: '待确认', WHS_PENDING: '等待补货',
  WHS_APPROVED: '已确认', WHS_REJECTED: '已退回',
})[status] || '当前订单不可审核'

function errorText(error) {
  const data = error?.data
  if (Array.isArray(data)) return data.join('；')
  return data?.detail || error?.message || '请求失败，请稍后重试'
}

// Shared page controller; API permissions remain authoritative for every operation.
export function useWarehouseReview({ api, auth, confirm, notify, id = null }) {
  const rows = ref([]), order = ref(null), options = ref({ owners: [], warehouses: [] })
  const filters = reactive({ stage: 'pending', search: '', owner_id: '', warehouse_id: '' })
  const page = ref(1), hasMore = ref(false), loading = ref(false), busy = ref(false)
  const error = ref(''), denied = ref(true), reason = ref('')
  const canAct = computed(() => !denied.value && auth.canReviewWarehouseOutbound && !loading.value && !busy.value)
  let alive = true, sequence = 0

  async function handleError(e) {
    if (!alive) return
    error.value = errorText(e)
    if ([401, 403].includes(Number(e?.statusCode || e?.status))) {
      denied.value = true
      rows.value = []
      order.value = null
      options.value = { owners: [], warehouses: [] }
      auth.invalidateWarehouseReviewCapability()
      await auth.loadProfile({ force: true }).catch(() => {})
      // A failed operation never re-enables controls merely because profile refresh succeeded.
    }
  }

  async function load({ more = false, authorize = false } = {}) {
    if (!alive || busy.value || (more && loading.value)) return
    const current = ++sequence
    loading.value = true
    error.value = ''
    try {
      if (authorize) {
        denied.value = true
        await auth.loadProfile({ force: true })
        if (!alive || current !== sequence) return
        if (!auth.canReviewWarehouseOutbound) throw { statusCode: 403, message: '没有当前仓库的出库审核权限' }
        denied.value = false
      }
      if (denied.value || !auth.canReviewWarehouseOutbound) return
      if (id !== null) {
        if (!Number.isInteger(unref(id)) || unref(id) <= 0) throw new Error('订单参数不正确')
        const data = await api.warehouseReviewDetail(unref(id))
        if (alive && current === sequence) order.value = data
      } else {
        const nextPage = more ? page.value + 1 : 1
        const [data, choices] = await Promise.all([
          api.warehouseReviewOrders({ ...filters, page: nextPage }),
          authorize ? api.warehouseReviewOptions() : Promise.resolve(null),
        ])
        if (!alive || current !== sequence) return
        rows.value = more ? [...rows.value, ...data.results] : data.results
        page.value = nextPage
        hasMore.value = Boolean(data.next)
        if (choices) options.value = choices
      }
    } catch (e) {
      if (current === sequence) await handleError(e)
    } finally {
      if (alive && current === sequence) loading.value = false
    }
  }

  async function act(operation) {
    const row = order.value
    if (!canAct.value || !row || !row[`can_warehouse_${operation}`]) return
    const text = reason.value.trim()
    if (operation === 'reject' && (!text || text.length > 500)) {
      error.value = '请填写 1 至 500 字的退回原因'
      return
    }
    busy.value = true
    error.value = ''
    let refresh = false, message = '', actionError = ''
    try {
      const accepted = await confirm(operation === 'confirm'
        ? '确认此订单并发布拣货任务？缺货时将进入等待补货。'
        : '确认退回？将释放库存分配，业务员需撤回修改后重新提交审核。')
      if (!accepted || !alive) return
      const payload = { expected_updated_at: row.updated_at, ...(operation === 'reject' ? { reason: text } : {}) }
      const data = await (operation === 'confirm' ? api.warehouseConfirm(row.id, payload) : api.warehouseReject(row.id, payload))
      if (!alive) return
      order.value = { ...data, can_warehouse_confirm: false, can_warehouse_reject: false }
      reason.value = ''
      refresh = true
      message = operation === 'reject' ? '订单已退回' : data.approval_status === 'WHS_PENDING' ? '等待补货，尚未发布拣货任务' : '已发布拣货任务'
    } catch (e) {
      await handleError(e)
      refresh = !denied.value && [400, 409].includes(Number(e?.statusCode || e?.status))
      actionError = errorText(e)
      message = Number(e?.statusCode || e?.status) === 409 ? '订单状态已变化，请核对刷新后的结果' : ''
    } finally {
      busy.value = false
      if (alive && refresh) await load()
      if (alive && actionError) error.value = actionError
      if (alive && message) notify(message)
    }
  }

  function dispose() { alive = false; sequence++ }
  return { rows, order, options, filters, page, hasMore, loading, busy, error, denied, reason, canAct, load, act, dispose }
}

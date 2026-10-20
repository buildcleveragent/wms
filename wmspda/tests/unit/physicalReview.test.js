import { describe, expect, it, vi } from 'vitest'
import { usePhysicalReview, formatQuantity } from '@/utils/physicalReview'

const row = { id: 7, task_no: 'JH-7', review_task: { id: 8, task_no: 'FH-8' }, can_review: true, can_retry_post: false }
const detail = (task = {}, lines = null) => ({
  task: { ...row, ...task },
  order: { id: 3, order_no: 'CK-3' },
  lines: lines || [{ id: 1, product_id: 2, product_name: '商品', order_line_id: 4, base_uom_id: 5, base_uom_name: '件', order_qty: '10', qty_plan: '10.000', qty_done: '10.000', picking_details: [] }],
  quantity_totals: [{ base_uom_id: 5, base_uom_name: '件', order_qty: '10', qty_plan: '10', qty_done: '10' }],
  content_error: '',
})
function setup(id = 7) {
  const api = {
    reviewPickTasks: vi.fn().mockResolvedValue({ results: [row], next: null }),
    pickReviewContext: vi.fn().mockResolvedValue(detail()),
    postPickTask: vi.fn().mockResolvedValue({ posted: true }),
  }
  const auth = { clearProfile: vi.fn(), loadProfile: vi.fn().mockResolvedValue({}) }
  const confirm = vi.fn().mockResolvedValue(true), notify = vi.fn()
  return { api, auth, confirm, notify, ...usePhysicalReview({ api, auth, confirm, notify, id }) }
}

describe('physical review workflow', () => {
  it('paginates and refreshes the list when returning after another reviewer finishes', async () => {
    const s = setup(null)
    s.api.reviewPickTasks.mockResolvedValueOnce({ results: [row], next: '?page=2' })
    await s.load()
    s.api.reviewPickTasks.mockResolvedValueOnce({ results: [{ ...row, id: 9 }], next: null })
    await s.load({ more: true })
    expect(s.api.reviewPickTasks).toHaveBeenLastCalledWith({ page: 2 })
    expect(s.rows.value.map(x => x.id)).toEqual([7, 9])
    s.api.reviewPickTasks.mockResolvedValueOnce({ results: [], next: null })
    await s.load()
    expect(s.rows.value).toEqual([])
    expect(s.page.value).toBe(1)
  })

  it('loads server review context and blocks self-review or inconsistent links', async () => {
    const s = setup()
    s.api.pickReviewContext.mockResolvedValue(detail({ can_review: false, blocked_reason: '不能自审' }))
    await s.load()
    expect(s.canSubmit.value).toBe(false)
    await s.submit()
    expect(s.api.postPickTask).not.toHaveBeenCalled()
  })

  it('prevents repeated clicks during confirmation and posting', async () => {
    const s = setup()
    await s.load()
    let accept
    s.confirm.mockImplementation(() => new Promise(resolve => { accept = resolve }))
    const pending = s.submit()
    await s.submit()
    expect(s.confirm).toHaveBeenCalledTimes(1)
    accept(true)
    await pending
    expect(s.api.postPickTask).toHaveBeenCalledTimes(1)
  })

  it('retains details after network failure but requires a successful refresh before retry', async () => {
    const s = setup()
    await s.load()
    s.api.postPickTask.mockRejectedValueOnce(new Error('网络失败'))
    await s.submit()
    expect(s.task.value.id).toBe(7)
    expect(s.lines.value).toHaveLength(1)
    expect(s.error.value).toBe('网络失败')
    expect(s.canSubmit.value).toBe(false)
    await s.submit()
    expect(s.api.postPickTask).toHaveBeenCalledTimes(1)
    await s.load()
    await s.submit()
    expect(s.api.postPickTask).toHaveBeenCalledTimes(2)
  })

  it('refreshes conflict state without losing the server explanation', async () => {
    const s = setup()
    await s.load()
    s.api.postPickTask.mockRejectedValue({ status: 409, data: { detail: '其他人已处理' } })
    s.api.pickReviewContext.mockResolvedValue(detail({ can_review: false, posting_status: 'POSTED' }))
    await s.submit()
    expect(s.task.value.posting_status).toBe('POSTED')
    expect(s.canSubmit.value).toBe(false)
    expect(s.error.value).toBe('其他人已处理')
  })

  it('supports approved/failed posting retries with a distinct confirmation', async () => {
    const s = setup()
    s.api.pickReviewContext.mockResolvedValue(detail({ can_review: false, can_retry_post: true }))
    await s.load()
    expect(s.canSubmit.value).toBe(true)
    await s.submit()
    expect(s.confirm).toHaveBeenCalledWith('确认重试库存过账？')
  })

  it('clears sensitive content and reloads permissions after revocation', async () => {
    const s = setup()
    await s.load()
    s.api.postPickTask.mockRejectedValue({ statusCode: 403, data: { detail: '权限失效' } })
    await s.submit()
    expect(s.denied.value).toBe(true)
    expect(s.task.value).toBeNull()
    expect(s.lines.value).toEqual([])
    expect(s.order.value).toBeNull()
    expect(s.totals.value).toEqual([])
    expect(s.canSubmit.value).toBe(false)
    expect(s.auth.clearProfile).toHaveBeenCalled()
    expect(s.auth.loadProfile).toHaveBeenCalledWith({ force: true })
  })

  it('keeps success read-only even if the subsequent refresh fails', async () => {
    const s = setup()
    await s.load()
    s.api.pickReviewContext.mockRejectedValue(new Error('刷新失败'))
    await s.submit()
    expect(s.task.value.posting_status).toBe('POSTED')
    expect(s.canSubmit.value).toBe(false)
  })

  it('ignores stale responses after a newer load or leaving the page', async () => {
    const s = setup(null)
    let resolve
    s.api.reviewPickTasks.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const old = s.load()
    await s.load()
    resolve({ results: [{ ...row, id: 99 }], next: null })
    await old
    expect(s.rows.value[0].id).toBe(7)
    s.dispose()
    await s.load()
    expect(s.api.reviewPickTasks).toHaveBeenCalledTimes(2)
  })

  it('trims quantity zeros while retaining significant decimals', () => {
    expect(formatQuantity('10.0000')).toBe('10')
    expect(formatQuantity('2.1250')).toBe('2.125')
  })

  it('clears stale details after the reported WmsTask 404 and disables submission', async () => {
    const s = setup()
    await s.load()
    s.api.pickReviewContext.mockRejectedValue({ statusCode: 404, data: { detail: 'No WmsTask matches the given query.' } })
    await s.load()
    expect(s.task.value).toBeNull()
    expect(s.lines.value).toEqual([])
    expect(s.totals.value).toEqual([])
    expect(s.error.value).toContain('No WmsTask')
    expect(s.canSubmit.value).toBe(false)
    await s.submit()
    expect(s.api.postPickTask).not.toHaveBeenCalled()
  })

  it('does not turn a malformed or old API response into an empty zero-quantity order', async () => {
    const s = setup()
    s.api.pickReviewContext.mockResolvedValue({ task: row, lines: [] })
    await s.load()
    expect(s.error.value).toContain('数据不完整')
    expect(s.task.value).toBeNull()
    expect(s.totals.value).toEqual([])
    expect(s.canSubmit.value).toBe(false)
  })

  it('keeps loaded evidence but disables submission when refreshing it fails', async () => {
    const s = setup()
    await s.load()
    s.api.pickReviewContext.mockRejectedValue(new Error('网络失败'))
    await s.load()
    expect(s.order.value.order_no).toBe('CK-3')
    expect(s.totals.value[0].qty_done).toBe('10')
    expect(s.canSubmit.value).toBe(false)
  })

  it('shows missing association errors without accepting an empty detail', async () => {
    const s = setup()
    s.api.pickReviewContext.mockResolvedValue({ ...detail({ can_review: false }, []), content_error: '来源订单异常', order: null, quantity_totals: [] })
    await s.load()
    expect(s.error.value).toBe('来源订单异常')
    expect(s.canSubmit.value).toBe(false)
    expect(s.totals.value).toEqual([])
  })
})

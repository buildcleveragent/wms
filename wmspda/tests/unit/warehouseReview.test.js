import { describe, expect, it, vi } from 'vitest'
import { useWarehouseReview, formatQuantity, amountSummaryLabel } from '@/utils/warehouseReview'

const row = {
  id: 7, updated_at: '2026-10-21T00:00:00Z', approval_status: 'OWNER_APPROVED',
  can_warehouse_confirm: true, can_warehouse_reject: true,
}
function setup(id = 7) {
  const api = {
    warehouseReviewDetail: vi.fn().mockResolvedValue({ ...row }),
    warehouseReviewOptions: vi.fn().mockResolvedValue({ owners: [], warehouses: [] }),
    warehouseReviewOrders: vi.fn().mockResolvedValue({ results: [row], next: null }),
    warehouseConfirm: vi.fn().mockResolvedValue({ ...row, approval_status: 'WHS_APPROVED' }),
    warehouseReject: vi.fn().mockResolvedValue({ ...row, approval_status: 'WHS_REJECTED' }),
  }
  const auth = { canReviewWarehouseOutbound: true, loadProfile: vi.fn().mockResolvedValue({}), invalidateWarehouseReviewCapability: vi.fn() }
  const confirm = vi.fn().mockResolvedValue(true), notify = vi.fn()
  const session = useWarehouseReview({ api, auth, confirm, notify, id })
  return { api, auth, confirm, notify, ...session }
}

describe('warehouse review behavior', () => {
  it('guards direct access and does not fetch orders without capability', async () => {
    const s = setup()
    s.auth.canReviewWarehouseOutbound = false
    await s.load({ authorize: true })
    expect(s.api.warehouseReviewDetail).not.toHaveBeenCalled()
    expect(s.denied.value).toBe(true)
  })

  it('filters, paginates, and resets pagination when changing stage', async () => {
    const s = setup(null)
    s.api.warehouseReviewOrders.mockResolvedValueOnce({ results: [row], next: '?page=2' })
    await s.load({ authorize: true })
    expect(s.hasMore.value).toBe(true)
    s.api.warehouseReviewOrders.mockResolvedValueOnce({ results: [{ ...row, id: 8 }], next: null })
    await s.load({ more: true })
    expect(s.rows.value.map(r => r.id)).toEqual([7, 8])
    s.filters.stage = 'rejected'; s.filters.owner_id = 2; s.filters.search = '客户'
    await s.load()
    expect(s.api.warehouseReviewOrders).toHaveBeenLastCalledWith({
      stage: 'rejected', owner_id: 2, warehouse_id: '', search: '客户', page: 1,
    })
    expect(s.page.value).toBe(1)
  })

  it('requires a reason and sends the reviewed version with a rejection', async () => {
    const s = setup()
    await s.load({ authorize: true })
    await s.act('reject')
    expect(s.api.warehouseReject).not.toHaveBeenCalled()
    s.reason.value = 'x'.repeat(501)
    await s.act('reject')
    expect(s.confirm).not.toHaveBeenCalled()
    s.reason.value = ' 地址错误 '
    await s.act('reject')
    expect(s.api.warehouseReject).toHaveBeenCalledWith(7, { expected_updated_at: row.updated_at, reason: '地址错误' })
    expect(s.notify).toHaveBeenCalledWith('订单已退回')
  })

  it('blocks double clicks while confirmation or the request is pending', async () => {
    const s = setup()
    await s.load({ authorize: true })
    let accept
    s.confirm.mockImplementation(() => new Promise(resolve => { accept = resolve }))
    const first = s.act('confirm')
    await s.act('confirm')
    expect(s.confirm).toHaveBeenCalledTimes(1)
    accept(true)
    await first
    expect(s.api.warehouseConfirm).toHaveBeenCalledTimes(1)
    expect(s.api.warehouseConfirm).toHaveBeenCalledWith(7, { expected_updated_at: row.updated_at })
  })

  it('reports waiting for replenishment separately from task release', async () => {
    const s = setup()
    await s.load({ authorize: true })
    s.api.warehouseConfirm.mockResolvedValue({ ...row, approval_status: 'WHS_PENDING' })
    await s.act('confirm')
    expect(s.notify).toHaveBeenCalledWith('等待补货，尚未发布拣货任务')
  })

  it('retains the page and rejection draft on network failure and permits retry', async () => {
    const s = setup()
    await s.load({ authorize: true })
    s.reason.value = '需核对'
    s.api.warehouseReject.mockRejectedValueOnce(new Error('网络失败'))
    await s.act('reject')
    expect(s.order.value.id).toBe(7)
    expect(s.reason.value).toBe('需核对')
    expect(s.error.value).toBe('网络失败')
    expect(s.canAct.value).toBe(true)
    await s.act('reject')
    expect(s.api.warehouseReject).toHaveBeenCalledTimes(2)
  })

  it('refreshes after version conflict, but does not repeat the mutation', async () => {
    const s = setup()
    await s.load({ authorize: true })
    s.api.warehouseConfirm.mockRejectedValue({ statusCode: 409, data: { detail: '过期' } })
    s.api.warehouseReviewDetail.mockResolvedValue({ ...row, can_warehouse_confirm: false, can_warehouse_reject: false })
    await s.act('confirm')
    await s.act('confirm')
    expect(s.api.warehouseConfirm).toHaveBeenCalledTimes(1)
    expect(s.api.warehouseReviewDetail).toHaveBeenCalledTimes(2)
    expect(s.order.value.can_warehouse_confirm).toBe(false)
  })

  it('keeps the concrete failure reason after refreshing a blocked action', async () => {
    const s = setup()
    await s.load({ authorize: true })
    s.api.warehouseConfirm.mockRejectedValue({ statusCode: 400, data: ['库存分配不足'] })
    await s.act('confirm')
    expect(s.api.warehouseReviewDetail).toHaveBeenCalledTimes(2)
    expect(s.error.value).toBe('库存分配不足')
    expect(s.api.warehouseConfirm).toHaveBeenCalledTimes(1)
  })

  it('revokes access immediately on 403 even if profile refresh succeeds', async () => {
    const s = setup()
    await s.load({ authorize: true })
    s.api.warehouseConfirm.mockRejectedValue({ statusCode: 403, data: { detail: '已撤权' } })
    await s.act('confirm')
    expect(s.order.value).toBeNull()
    expect(s.canAct.value).toBe(false)
    expect(s.auth.invalidateWarehouseReviewCapability).toHaveBeenCalled()
    expect(s.auth.loadProfile).toHaveBeenCalledTimes(2)
  })

  it('ignores stale list responses and requests completed after leaving a page', async () => {
    const s = setup(null)
    await s.load({ authorize: true })
    let finish
    s.api.warehouseReviewOrders.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const old = s.load()
    s.filters.stage = 'approved'
    await s.load()
    finish({ results: [{ id: 99 }], next: null })
    await old
    expect(s.rows.value[0].id).toBe(7)
    s.dispose()
    await s.load()
    expect(s.api.warehouseReviewOrders).toHaveBeenCalledTimes(3)
  })

  it('preserves meaningful quantity precision and optional-pricing labels', () => {
    expect(formatQuantity('96.0000')).toBe('96')
    expect(formatQuantity('1.2300')).toBe('1.23')
    expect(amountSummaryLabel({ amount_status: 'UNPRICED' })).toBe('未计价')
    expect(amountSummaryLabel({ amount_status: 'PARTIAL', priced_amount: '12.50', unpriced_line_count: 2 }))
      .toBe('已计价小计 ¥12.50，另有 2 行未计价')
  })
})

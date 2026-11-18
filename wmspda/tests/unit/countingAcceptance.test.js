import { reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { countPayload, countStatus, hasUnsavedCounts, validCountQuantity, useCountSession } from '@/utils/counting'

function setup() {
  const auth = reactive({
    access: 'test-account', canManageCount: true,
    profile: { capabilities: { can_manage_count: true } },
    loadProfile: vi.fn().mockResolvedValue({}),
  })
  const methods = ['createCountTask', 'releaseCountTask', 'cancelCountTask', 'approveCountTask',
    'rejectCountTask', 'postCountTask', 'claimCountTask', 'submitCountTask', 'recordCount']
  const api = Object.fromEntries(methods.map(name => [name, vi.fn().mockResolvedValue({ id: 8 })]))
  Object.assign(api, { countTasks: vi.fn().mockResolvedValue([]),
    countTaskDetail: vi.fn().mockResolvedValue({ id: 8 }), countTaskLines: vi.fn().mockResolvedValue([]) })
  const confirm = vi.fn().mockResolvedValue(true)
  return { auth, api, confirm, ...useCountSession({ api, auth, confirm }) }
}

describe('count acceptance boundaries and session recovery', () => {
  it.each(['0', '0.0001', '99999999999999.9999', '000099999999999999.9999'])('accepts exact count string %s', value => {
    expect(validCountQuantity(value)).toBe(true)
  })
  it.each(['', '-1', '0.00001', '100000000000000', '99999999999999.99999', 'NaN', 'Infinity', '1e2'])('rejects unsupported count string %s', value => {
    expect(validCountQuantity(value)).toBe(false)
  })
  it('removes stale detail and actions when access is no longer available', async () => {
    const s = setup()
    s.task.value = { id: 8, can_record: true }
    s.lines.value = [{ id: 7, qty_counted: '5' }]
    s.api.countTaskDetail.mockRejectedValueOnce({ statusCode: 404, message: '任务不可访问' })
    await s.load({ id: 8 })
    expect(s.task.value).toBeNull()
    expect(s.lines.value).toEqual([])
    expect(s.error.value).toBe('任务不可访问')
    s.dispose()
  })
  const form = { warehouse_id: 1, owner_id: 2, scope: 'ALL', blind: true,
    exclude_zero_onhand: true, max_lines: '1000', recount_threshold: '0' }

  it.each([1, 1000, 10000])('accepts the documented line limit %s', max => {
    expect(countPayload({ ...form, max_lines: String(max) }).max_lines).toBe(max)
  })
  it.each(['', '-1', '0.00001', '10000000000', 'NaN', 'Infinity'])('rejects threshold %s', threshold => {
    expect(() => countPayload({ ...form, recount_threshold: threshold })).toThrow()
  })
  it.each(['ALL', 'ZONE', 'LOC', 'SKU'])('removes stale scope selections for %s', scope => {
    const result = countPayload({ ...form, scope, zone_type: 2, location_id: 3,
      product_id: 4, location_prefix: 'A-', subwarehouse_id: 5 })
    expect(result.subwarehouse_id).toBe(5)
    expect(result.zone_type).toBe(scope === 'ZONE' ? 2 : undefined)
    expect(result.location_id).toBe(scope === 'LOC' ? 3 : undefined)
    expect(result.product_id).toBe(scope === 'SKU' ? 4 : undefined)
    expect(result.location_prefix).toBeUndefined()
  })
  it.each(['release', 'cancel', 'approve', 'reject', 'post', 'submit'])('cancelled %s confirmation performs no write', async action => {
    const s = setup()
    s.task.value = { id: 8 }
    s.confirm.mockResolvedValueOnce(false)
    expect(await s.act(action, {}, 'Confirm')).toBeNull()
    expect(Object.values(s.api).every(fn => fn.mock.calls.length === 0)).toBe(true)
    expect(s.busy.value).toBe(false)
    s.dispose()
  })
  it('clears old account detail and discards a write after switching during confirmation', async () => {
    const s = setup()
    let confirm
    s.task.value = { id: 8 }
    s.lines.value = [{ id: 7, qty_book: '12' }]
    s.confirm.mockImplementationOnce(() => new Promise(resolve => { confirm = resolve }))
    const action = s.act('post', {}, 'Confirm')
    s.auth.access = 'another-account'
    confirm(true)
    expect(await action).toBeNull()
    expect(s.task.value).toBeNull()
    expect(s.lines.value).toEqual([])
    expect(s.api.postCountTask).not.toHaveBeenCalled()
    s.dispose()
  })
  it('retains no partial detail if the lines request fails and can reload', async () => {
    const s = setup()
    s.api.countTaskLines.mockRejectedValueOnce({ statusCode: 500, message: '明细读取失败' })
    await s.load({ id: 8 })
    expect(s.task.value).toBeNull()
    expect(s.lines.value).toEqual([])
    expect(s.error.value).toBe('明细读取失败')
    await s.load({ id: 8 })
    expect(s.task.value.id).toBe(8)
    expect(s.error.value).toBe('')
    s.dispose()
  })
  it('stops loading and does not fetch tasks after failed authentication refresh', async () => {
    const s = setup()
    s.auth.loadProfile.mockRejectedValueOnce({ statusCode: 401, message: '登录已过期' })
    await s.load({ refreshAuth: true })
    expect(s.api.countTasks).not.toHaveBeenCalled()
    expect(s.loading.value).toBe(false)
    expect(s.error.value).toBe('登录已过期')
    s.dispose()
  })
  it('retries a failed record with the same caller supplied idempotency key', async () => {
    const s = setup()
    s.task.value = { id: 8 }
    const payload = { line_id: 3, qty_counted: '0', client_seq: 'retry-key' }
    s.api.recordCount.mockRejectedValueOnce({ message: '网络异常' })
    expect(await s.act('record', payload)).toBeNull()
    await s.act('record', payload)
    expect(s.api.recordCount.mock.calls).toEqual([[8, payload], [8, payload]])
    expect(s.busy.value).toBe(false)
    s.dispose()
  })
  it.each([['0', '0.0000', false], ['0005.1200', '5.12', false], ['', '0', true], ['0.0001', '0', true]])(
    'detects unsaved input %s against %s', (value, saved, expected) => {
      expect(hasUnsavedCounts([{ count_status: 'COUNTED', _qty: value, qty_counted: saved }])).toBe(expected)
    })
  it.each([
    [{ status: 'CANCELLED' }, '已取消'],
    [{ status: 'COMPLETED', review_status: 'REJECTED' }, '已驳回'],
    [{ status: 'COMPLETED', review_status: 'APPROVED', posting_status: 'FAILED' }, '过账失败，可重试'],
    [{ status: 'COMPLETED', review_status: 'APPROVED', posting_status: 'POSTED' }, '已过账'],
  ])('renders final task status consistently', (task, label) => {
    expect(countStatus(task)).toBe(label)
  })
})

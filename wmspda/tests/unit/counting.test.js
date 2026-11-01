import { describe, expect, it, vi } from 'vitest'
import { reactive, nextTick } from 'vue'
import { countPayload, countStatus, countError, sameCountQuantity, hasUnsavedCounts, useCountSession } from '@/utils/counting'
const draft = () => ({ warehouse_id: 1, owner_id: 2, scope: 'ALL', blind: true, exclude_zero_onhand: true, max_lines: '1000', recount_threshold: '0' })
function setup() {
  const auth = reactive({ access: 'token-a', user: { id: 1 }, canManageCount: true, profile: { capabilities: { can_manage_count: true } }, loadProfile: vi.fn().mockResolvedValue({}) })
  const api = { countTasks: vi.fn().mockResolvedValue([]), countTaskDetail: vi.fn().mockResolvedValue({ id: 9 }), countTaskLines: vi.fn().mockResolvedValue([]), createCountTask: vi.fn().mockResolvedValue({ id: 9 }), releaseCountTask: vi.fn().mockResolvedValue({ id: 9 }), postCountTask: vi.fn().mockResolvedValue({ posted: true }) }
  const confirm = vi.fn().mockResolvedValue(true)
  return { auth, api, confirm, ...useCountSession({ api, auth, confirm }) }
}
describe('PDA count management', () => {
  it('uses the request layer sanitized message for non-JSON server errors', () => {
    expect(countError({ data: '<html>debug traceback</html>', message: '服务器异常' })).toBe('服务器异常')
  })
  it('compares decimal inputs without floating point rounding', () => {
    expect(sameCountQuantity('00.0', '0.0000')).toBe(true)
    expect(sameCountQuantity('99999999999999.0001', '99999999999999.0002')).toBe(false)
  })
  it('requires saving changed counts, including clearing a saved value', () => {
    expect(hasUnsavedCounts([{ count_status: 'COUNTED', _qty: '', qty_counted: '5' }])).toBe(true)
    expect(hasUnsavedCounts([{ count_status: 'COUNTED', _qty: '0', qty_counted: '0.0000' }])).toBe(false)
    expect(hasUnsavedCounts([{ count_status: 'NOT_COUNTED', _qty: '' }])).toBe(false)
  })
  it('builds scope-specific payloads without hidden selections', () => {
    expect(countPayload({ ...draft(), location_id: 8, product_id: 9 })).not.toHaveProperty('product_id')
    expect(countPayload({ ...draft(), scope: 'SKU', product_id: 9 })).toMatchObject({ product_id: 9, max_lines: 1000 })
    expect(countPayload({ ...draft(), scope: 'LOC', location_prefix: ' A-01 ' })).toHaveProperty('location_prefix', 'A-01')
  })
  it.each([{ owner_id: null }, { scope: 'SKU' }, { scope: 'LOC' }, { scope: 'ZONE' }, { max_lines: '0' }, { max_lines: '10001' }, { max_lines: '1.5' }, { recount_threshold: '-1' }])('rejects invalid form %j', change => {
    expect(() => countPayload({ ...draft(), ...change })).toThrow()
  })
  it('guards manager lists and creation', async () => {
    const s = setup(); s.auth.canManageCount = false
    await s.load({ stage: 'draft' }); await s.act('create', draft())
    expect(s.api.countTasks).not.toHaveBeenCalled(); expect(s.api.createCountTask).not.toHaveBeenCalled()
  })
  it('ignores stale list responses', async () => {
    const s = setup(); let finish
    s.api.countTasks.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const first = s.load({ stage: 'draft' })
    s.api.countTasks.mockResolvedValueOnce([{ id: 2 }]); await s.load({ stage: 'posting' })
    finish([{ id: 1 }]); await first
    expect(s.rows.value).toEqual([{ id: 2 }]); expect(s.loading.value).toBe(false)
  })
  it('blocks repeated writes during confirmation', async () => {
    const s = setup(); s.task.value = { id: 9 }; let accept
    s.confirm.mockImplementation(() => new Promise(resolve => { accept = resolve }))
    const first = s.act('release', {}, '确认'); await s.act('release', {}, '确认')
    expect(s.confirm).toHaveBeenCalledTimes(1)
    accept(true); await first; expect(s.api.releaseCountTask).toHaveBeenCalledTimes(1)
  })
  it('does not mutate while refreshing task state', async () => {
    const s = setup(); s.task.value = { id: 9 }; let finish
    s.api.countTaskDetail.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const reading = s.load({ id: 9 })
    await s.act('release')
    expect(s.api.releaseCountTask).not.toHaveBeenCalled()
    finish({ id: 9 }); await reading
  })
  it('retains a draft after a failed release and permits retry', async () => {
    const s = setup(); s.task.value = { id: 9, status: 'DRAFT' }
    s.api.releaseCountTask.mockRejectedValueOnce({ statusCode: 400, data: ['范围冲突'] })
    expect(await s.act('release')).toBeNull(); expect(s.error.value).toBe('范围冲突')
    expect(s.task.value.status).toBe('DRAFT'); expect(await s.act('release')).toEqual({ id: 9 })
  })
  it('revokes access after 403', async () => {
    const s = setup(); s.task.value = { id: 9 }
    s.api.postCountTask.mockRejectedValueOnce({ statusCode: 403, data: { detail: '已撤权' } })
    await s.act('post'); expect(s.manager.value).toBe(false); expect(s.task.value).toBeNull()
    await s.act('post'); expect(s.api.postCountTask).toHaveBeenCalledTimes(1)
  })
  it('ignores responses after switching account or leaving', async () => {
    const s = setup(); let finish
    s.api.countTasks.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const loading = s.load(); s.auth.access = 'token-b'; await nextTick(); finish([{ id: 1 }]); await loading
    expect(s.rows.value).toEqual([])
    s.api.createCountTask.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const creating = s.act('create', draft()); s.invalidate(); finish({ id: 10 })
    expect(await creating).toBeNull()
  })
  it('distinguishes failed and completed posting', () => {
    expect(countStatus({ posting_status: 'FAILED' })).toContain('重试')
    expect(countStatus({ posting_status: 'POSTED' })).toBe('已过账')
  })
})

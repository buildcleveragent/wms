import { describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import { useOutboundHistory, queryString, quantity } from '@/utils/outboundHistory'
function setup(config = {}) {
  const auth = reactive({ access: 'account-one', user: { id: 1 }, profileLoaded: true, canProcessAssistedOutbound: true,
    loadProfile: vi.fn().mockResolvedValue({}), invalidateAssistedCapability: vi.fn() })
  const api = {
    outboundHistoryOptions: vi.fn().mockResolvedValue({ owners: [{ id: 1, name: '货主甲' }] }),
    outboundHistory: vi.fn().mockResolvedValue({ results: [{ id: 1 }], count: 2, next: '?page=2' }),
    outboundHistoryStats: vi.fn().mockResolvedValue({ summary: { order_count: 2 } }),
    outboundHistoryDetail: vi.fn().mockResolvedValue({ id: 1, lines: [] }),
    assistedOutboundHistoryOptions: vi.fn().mockResolvedValue({ owners: [] }),
    assistedOutboundHistory: vi.fn().mockResolvedValue({ results: [{ id: 2 }], count: 1, next: null }),
    assistedOutboundStats: vi.fn().mockResolvedValue({ summary: { order_count: 1 } }),
  }
  return { api, auth, ...useOutboundHistory({ api, auth, ...config }) }
}
const deferred = () => { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j }); return { promise, resolve, reject } }
describe('outbound history queries', () => {
  it('paginates the applied query even while filters are edited', async () => {
    const s = setup(); s.filters.search = '旧名称'; await s.initialize()
    s.filters.search = '新名称'; s.api.outboundHistory.mockResolvedValueOnce({ results: [{ id: 3 }], count: 2, next: null })
    await s.load({ more: true })
    expect(s.api.outboundHistory).toHaveBeenLastCalledWith(expect.objectContaining({ search: '旧名称', page: 2 }))
    expect(s.rows.value.map(x => x.id)).toEqual([1, 3]); expect(s.hasMore.value).toBe(false)
    await s.load(); expect(s.api.outboundHistory).toHaveBeenLastCalledWith(expect.objectContaining({ search: '新名称', page: 1 })); s.dispose()
  })
  it('keeps rows and retries the failed page with its original query', async () => {
    const s = setup(); await s.initialize(); s.api.outboundHistory.mockRejectedValueOnce(new Error('网络中断'))
    await s.load({ more: true }); expect(s.rows.value).toEqual([{ id: 1 }]); expect(s.page.value).toBe(1)
    s.filters.search = '尚未查询'; await s.retry()
    expect(s.api.outboundHistory).toHaveBeenLastCalledWith(expect.objectContaining({ search: '', page: 2 })); s.dispose()
  })
  it('ignores old responses after a new query including old failures', async () => {
    const s = setup(); await s.initialize(); const old = deferred(), latest = deferred()
    s.api.outboundHistory.mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise)
    s.filters.search = 'first'; const a = s.load(); s.filters.search = 'second'; const b = s.load()
    latest.resolve({ results: [{ id: 22 }], count: 1, next: null }); await b
    old.reject({ statusCode: 403, message: '旧请求' }); await a
    expect(s.rows.value).toEqual([{ id: 22 }]); expect(s.authorized.value).toBe(true); expect(s.error.value).toBe(''); s.dispose()
  })
  it('binds statistic drilldown and displayed period to successful filters', async () => {
    const s = setup({ statsMode: true }); s.applyRoute({ start_date: '2026-02-01', end_date: '2026-02-28', is_closed: 'false' }); await s.initialize()
    expect(s.dirty.value).toBe(false)
    s.filters.end_date = '2026-03-31'; expect(s.dirty.value).toBe(true); expect(s.periodLabel.value).toBe('2026-02-01 至 2026-02-28')
    expect(queryString(s.applied.value)).toContain('is_closed=false')
    s.api.outboundHistoryStats.mockRejectedValueOnce(new Error('失败')); await s.load()
    expect(s.stats.value).toBeNull(); expect(s.applied.value).toBeNull(); s.dispose()
  })
  it('rejects reversed, invalid and oversized statistics periods before fetching', async () => {
    const s = setup({ statsMode: true }); await s.initialize(); s.api.outboundHistoryStats.mockClear()
    for (const [start, end] of [['2026-03-01', '2026-02-28'], ['2026-02-30', '2026-03-01'], ['2024-01-01', '2025-01-01']]) {
      s.filters.start_date = start; s.filters.end_date = end; await s.load(); expect(s.error.value).not.toBe('')
    }
    expect(s.api.outboundHistoryStats).not.toHaveBeenCalled(); s.dispose()
  })
  it('clears all account data and discards in-flight results on account switch', async () => {
    const s = setup(); await s.initialize(); const pending = deferred(); s.api.outboundHistory.mockReturnValueOnce(pending.promise)
    s.filters.search = '私有客户'; const request = s.load(); s.auth.access = 'account-two'; s.auth.user = { id: 2 }
    expect(s.filters.search).toBe(''); expect(s.options.value).toEqual({}); expect(s.authorized.value).toBe(false)
    pending.resolve({ results: [{ id: 123 }], count: 1 }); await request
    expect(s.rows.value).toEqual([]); expect(s.applied.value).toBeNull(); s.dispose()
  })
  it('clears protected data on permission loss and refreshes profile on return', async () => {
    const s = setup({ assisted: true }); await s.initialize()
    s.api.assistedOutboundHistory.mockRejectedValueOnce({ statusCode: 403, message: '无权限' }); await s.load()
    expect(s.rows.value).toEqual([]); expect(s.authorized.value).toBe(false); expect(s.auth.invalidateAssistedCapability).toHaveBeenCalled()
    await s.initialize(); expect(s.auth.loadProfile).toHaveBeenCalledTimes(2); s.dispose()
  })
  it('does not query assisted data without its capability', async () => {
    const s = setup({ assisted: true }); s.auth.canProcessAssistedOutbound = false; await s.initialize()
    expect(s.api.assistedOutboundHistory).not.toHaveBeenCalled(); expect(s.error.value).toContain('无代货主'); s.dispose()
  })
  it('suspends hidden pages and refreshes on return without accepting old responses', async () => {
    const s = setup(); await s.initialize(); const pending = deferred(); s.api.outboundHistory.mockReturnValueOnce(pending.promise)
    const request = s.load(); s.suspend(); pending.resolve({ results: [{ id: 44 }], count: 1 }); await request
    expect(s.rows.value).toEqual([]); await s.initialize(); expect(s.rows.value).toEqual([{ id: 1 }]); s.dispose()
  })
  it('loads strict history detail and validates ids', async () => {
    const s = setup({ detailId: 7 }); await s.initialize()
    expect(s.api.outboundHistoryDetail).toHaveBeenCalledWith(7); expect(s.api.outboundHistoryOptions).not.toHaveBeenCalled(); s.dispose()
    const bad = setup({ detailId: 0 }); await bad.initialize(); expect(bad.api.outboundHistoryDetail).not.toHaveBeenCalled(); expect(bad.error.value).toContain('参数'); bad.dispose()
  })
  it('formats decimal quantities without losing precision', () => {
    expect(quantity('12345678901234567890.120')).toBe('12345678901234567890.12'); expect(quantity('0.000')).toBe('0')
    expect(queryString({ is_closed: false, owner_id: '', search: '品牌&型号' })).toBe('is_closed=false&search=%E5%93%81%E7%89%8C%26%E5%9E%8B%E5%8F%B7')
  })
})

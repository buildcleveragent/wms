import { afterEach, describe, expect, it, vi } from 'vitest'
import { useHomeTodos } from '@/utils/homeTodos'
const response = (count, scope = 'warehouse-1') => ({ scope, items: [{ key: 'pick', count }] })
async function settle() { await Promise.resolve(); await Promise.resolve() }
function setup() {
  vi.useFakeTimers()
  const fetchTodos = vi.fn().mockResolvedValue(response(2))
  return { fetchTodos, ...useHomeTodos({ fetchTodos }) }
}
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })
describe('home reminders', () => {
  it('reports synchronous frontend failures that never create a network request', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => {})
    const s = setup()
    s.fetchTodos.mockImplementationOnce(() => { throw new TypeError('api.homeTodos is not a function') })
    s.show(); await settle()
    expect(s.error.value).toContain('待办前端执行异常（TypeError）')
    expect(log).toHaveBeenCalledWith('[HomeTodos] 前端执行异常：', 'TypeError', 'api.homeTodos is not a function')
    expect(s.loading.value).toBe(false)
    await s.refresh()
    expect(s.loaded.value).toBe(true); expect(s.error.value).toBe('')
  })
  it.each([
    [401, '登录已失效'],
    [403, '仓库角色和范围'],
    [404, '后端地址和服务版本'],
    [500, 'HTTP 500'],
    [503, 'HTTP 503'],
    [0, '无法连接待办服务'],
  ])('explains request failure %s and recovers after retry', async (statusCode, message) => {
    const s = setup(); s.show(); await settle()
    s.fetchTodos.mockRejectedValueOnce({ statusCode, message: '<html>private server output</html>' })
    await s.refresh()
    expect(s.error.value).toContain(message)
    expect(s.error.value).not.toContain('private server output')
    expect(s.items.value).toEqual([]); expect(s.popup.value).toBe(false)
    await s.refresh()
    expect(s.error.value).toBe(''); expect(s.loaded.value).toBe(true)
  })
  it('distinguishes malformed successful responses from connection errors', async () => {
    const s = setup()
    s.fetchTodos.mockResolvedValue('<html>wrong backend</html>')
    s.show(); await settle()
    expect(s.error.value).toContain('待办接口返回格式异常')
    expect(s.loaded.value).toBe(false)
  })
  it('opens on first nonzero response and increases only, using decreased counts as baseline', async () => {
    const s = setup(); s.show(); await settle()
    expect(s.popup.value).toBe(true); s.popup.value = false
    await s.refresh(); expect(s.popup.value).toBe(false)
    s.fetchTodos.mockResolvedValue(response(1)); await s.refresh()
    expect(s.popup.value).toBe(false)
    s.fetchTodos.mockResolvedValue(response(2)); await s.refresh()
    expect(s.popup.value).toBe(true)
    s.fetchTodos.mockResolvedValue(response(0)); await s.refresh()
    expect(s.popup.value).toBe(false); expect(s.loaded.value).toBe(true)
  })
  it('refreshes each minute only while visible and preserves dismissal on return', async () => {
    const s = setup(); s.show(); await settle(); s.popup.value = false
    await vi.advanceTimersByTimeAsync(60000); expect(s.fetchTodos).toHaveBeenCalledTimes(2)
    s.hide(); await vi.advanceTimersByTimeAsync(120000); expect(s.fetchTodos).toHaveBeenCalledTimes(2)
    s.show(); await settle(); expect(s.fetchTodos).toHaveBeenCalledTimes(3)
    expect(s.popup.value).toBe(false)
  })
  it('ignores responses after leaving and prevents overlapping requests', async () => {
    const s = setup(); let done
    s.fetchTodos.mockImplementationOnce(() => new Promise(resolve => { done = resolve }))
    s.show(); await s.refresh(); expect(s.fetchTodos).toHaveBeenCalledTimes(1)
    s.hide(); s.show(); await settle()
    done(response(90)); await settle(); expect(s.items.value[0].count).toBe(2)
  })
  it('clears old counts on failure without treating errors as zero', async () => {
    const s = setup(); s.show(); await settle()
    s.fetchTodos.mockRejectedValueOnce(new Error('offline')); await s.refresh()
    expect(s.error.value).toBe('更新失败，请重试'); expect(s.loaded.value).toBe(false)
    expect(s.items.value).toEqual([]); expect(s.popup.value).toBe(false)
    await s.refresh(); expect(s.items.value[0].count).toBe(2); expect(s.popup.value).toBe(false)
  })
  it('resets on account, permissions or warehouse scope change', async () => {
    const s = setup(); s.show(); await settle(); s.popup.value = false
    s.fetchTodos.mockResolvedValue(response(1, 'warehouse-2')); await s.refresh()
    expect(s.popup.value).toBe(true)
    s.fetchTodos.mockRejectedValueOnce({ statusCode: 403 }); await s.refresh()
    expect(s.items.value).toEqual([])
    s.reset(); await settle(); expect(s.popup.value).toBe(true)
  })
  it('updates an open popup and compares each category independently', async () => {
    const s = setup(); s.show(); await settle()
    s.fetchTodos.mockResolvedValue({ scope: 'warehouse-1', items: [{ key: 'pick', count: 1 }, { key: 'receive', count: 1 }] })
    await s.refresh(); expect(s.pending.value.map(i => i.count)).toEqual([1, 1]); expect(s.popup.value).toBe(true)
  })
})
it('shows zero normally, rejects invalid counts, and alerts when work first arrives', async () => {
  const s = setup(); s.fetchTodos.mockResolvedValue(response(0)); s.show(); await settle()
  expect(s.loaded.value).toBe(true); expect(s.popup.value).toBe(false)
  s.fetchTodos.mockResolvedValue(response(-1)); await s.refresh()
  expect(s.loaded.value).toBe(false); expect(s.error.value).toBeTruthy()
  s.fetchTodos.mockResolvedValue(response(1)); await s.refresh()
  expect(s.popup.value).toBe(true)
})

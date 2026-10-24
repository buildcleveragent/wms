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

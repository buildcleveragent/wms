// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { defineComponent, ref, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useRefreshPreferences } from '../store/refreshPreferences'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@dcloudio/uni-app', () => ({ onShow: vi.fn(), onHide: vi.fn(), onUnload: vi.fn() }))
vi.mock('../utils/request', () => ({ getStoredToken: () => 'session-a' }))
import { useLiveResource } from '../utils/use-live-resource'

describe('live resource context and retained reading state', () => {
  let wrapper
  beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('uni', {}); setActivePinia(createPinia()); useRefreshPreferences().$patch({ automatic: true, minutes: 1 }) })
  afterEach(() => { wrapper?.unmount(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })

  async function harness(load, key, hold = () => false, compare) {
    let resource
    wrapper = mount(defineComponent({ setup() { resource = useLiveResource(load, { key, hold, compare }); return () => null } }))
    await nextTick()
    return resource
  }

  it('drops a response from a previous warehouse and starts the new request after it', async () => {
    const scope = ref('A')
    let finish
    const load = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      .mockResolvedValue({ results: [{ id: 'B:1' }] })
    const resource = await harness(load, () => scope.value)
    scope.value = 'B'
    await nextTick()
    expect(resource.data.value).toBeNull()
    finish({ results: [{ id: 'A:1' }] })
    await vi.advanceTimersByTimeAsync(1)
    expect(resource.data.value.results).toEqual([{ id: 'B:1' }])
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('retains reading position and publishes changed data only after acceptance', async () => {
    const load = vi.fn().mockResolvedValueOnce({ results: [{ id: 'old' }] })
      .mockResolvedValue({ results: [{ id: 'new' }] })
    const resource = await harness(load, () => 'same', () => true)
    await vi.advanceTimersByTimeAsync(60000)
    expect(resource.data.value.results[0].id).toBe('old')
    expect(resource.pending.value.results[0].id).toBe('new')
    resource.apply()
    expect(resource.data.value.results[0].id).toBe('new')
  })

  it('retains a network failure but removes revoked-scope data', async () => {
    const load = vi.fn().mockResolvedValueOnce({ results: [{ id: 'visible' }] })
      .mockRejectedValueOnce({ statusCode: 0 }).mockRejectedValue({ statusCode: 403 })
    const resource = await harness(load, () => 'same')
    await vi.advanceTimersByTimeAsync(60000)
    expect(resource.data.value.results[0].id).toBe('visible')
    await vi.advanceTimersByTimeAsync(120000)
    expect(resource.data.value).toBeNull()
  })

  it('detects bill changes while ignoring response timestamps', async () => {
    const load = vi.fn().mockResolvedValueOnce({ id: 1, total: '20.00', meta: { generated_at: 'old' } })
      .mockResolvedValueOnce({ id: 1, total: '20.00', meta: { generated_at: 'new' } })
      .mockResolvedValue({ id: 1, total: '30.00', meta: { generated_at: 'later' } })
    const resource = await harness(load, () => 'bill', () => true)
    await vi.advanceTimersByTimeAsync(60000)
    expect(resource.pending.value).toBeNull()
    await vi.advanceTimersByTimeAsync(60000)
    expect(resource.pending.value.total).toBe('30.00')
    expect(resource.data.value.total).toBe('20.00')
  })

  it('keeps a reader on the previous business day until accepting the new day', async () => {
    const load = vi.fn().mockResolvedValueOnce({ scope: { date_to: '2026-11-01' }, results: [] })
      .mockResolvedValue({ scope: { date_to: '2026-11-02' }, results: [] })
    const resource = await harness(load, () => 'today', () => true)
    await vi.advanceTimersByTimeAsync(60000)
    expect(resource.data.value.scope.date_to).toBe('2026-11-01')
    expect(resource.pending.value.scope.date_to).toBe('2026-11-02')
    resource.apply()
    expect(resource.data.value.scope.date_to).toBe('2026-11-02')
  })
  it('does not announce a refreshed digest token as a new business event', async () => {
    const load = vi.fn().mockResolvedValueOnce({ token: 'one', count: 2 })
      .mockResolvedValueOnce({ token: 'two', count: 2 }).mockResolvedValue({ token: 'three', count: 3 })
    const resource = await harness(load, () => 'digest', () => true, value => value.count)
    await vi.advanceTimersByTimeAsync(60000)
    expect(resource.pending.value).toBeNull()
    await vi.advanceTimersByTimeAsync(60000)
    expect(resource.data.value.count).toBe(2)
    expect(resource.pending.value.count).toBe(3)
  })

  it('off loads on entry and filtering, but not on visibility or network recovery', async () => {
    useRefreshPreferences().automatic = false
    let network
    uni.onNetworkStatusChange = callback => { network = callback }
    const key = ref('A'), load = vi.fn().mockResolvedValue({ count: 1 })
    const resource = await harness(load, () => key.value)
    await vi.advanceTimersByTimeAsync(3600000)
    expect(load).toHaveBeenCalledTimes(1)
    network({ isConnected: false }); network({ isConnected: true })
    await nextTick()
    expect(load).toHaveBeenCalledTimes(1)
    await resource.refresh()
    expect(load).toHaveBeenCalledTimes(2)
    key.value = 'B'; await nextTick()
    expect(load).toHaveBeenCalledTimes(3)
    useRefreshPreferences().automatic = true
    await nextTick()
    expect(load).toHaveBeenCalledTimes(4)
    await vi.advanceTimersByTimeAsync(60000)
    expect(load).toHaveBeenCalledTimes(5)
  })

})

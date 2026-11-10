import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRefreshPreferences } from '../store/refreshPreferences'
let storage
beforeEach(() => {
  setActivePinia(createPinia())
  storage = new Map()
  global.uni = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value) }
})
it('defaults off, persists per account/device and survives logout', () => {
  const prefs = useRefreshPreferences()
  prefs.restore({ id: 1 })
  expect(prefs.automatic).toBe(false)
  expect(prefs.minutes).toBe(5)
  prefs.save(true, 10)
  prefs.restore(null)
  expect(prefs.automatic).toBe(false)
  prefs.restore({ id: 2 })
  expect(prefs.automatic).toBe(false)
  prefs.restore({ id: 1 })
  expect(prefs.automatic).toBe(true)
  expect(prefs.minutes).toBe(10)
  setActivePinia(createPinia())
  const restarted = useRefreshPreferences()
  restarted.restore({ id: 1 })
  expect(restarted.minutes).toBe(10)
})
it('rejects invalid minutes and does not apply a failed save', () => {
  const prefs = useRefreshPreferences()
  prefs.restore({ id: 1 })
  for (const value of [0, 1441, 1.5, NaN, '5']) expect(() => prefs.save(true, value)).toThrow()
  uni.setStorageSync = vi.fn(() => { throw new Error('disk full') })
  expect(() => prefs.save(true, 3)).toThrow('disk full')
  expect(prefs.automatic).toBe(false)
  expect(prefs.minutes).toBe(5)
})

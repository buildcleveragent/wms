import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
vi.mock('../utils/request', () => ({ api: { bossContext: vi.fn() } }))
import { api } from '../utils/request'
import { useBossScope } from '../store/bossScope'
let storage
beforeEach(() => {
  setActivePinia(createPinia())
  storage = new Map()
  global.uni = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key) }
  api.bossContext.mockResolvedValue({ business_today: '2024-03-01', defaults: { date_from: '2024-03-01', date_to: '2024-03-01' }, warehouse_options: [], owner_options: [], limits: { max_range_days: null } })
})
it('uses server defaults, independent of device date', async () => {
  const scope = useBossScope()
  await scope.loadContext({ id: 1 })
  expect(scope.date_from).toBe('2024-03-01')
  expect(scope.date_to).toBe('2024-03-01')
  expect(scope.setDates('2000-01-01', '2024-03-01')).toBe(true)
  expect(scope.setDates('2024-01-01', '2024-03-02')).toBe(false)
})
it('keeps saved historical ranges and supports server midnight refresh', async () => {
  storage.set('boss_scope:1', JSON.stringify({ date_from: '2000-01-01', date_to: '2023-12-31' }))
  const scope = useBossScope()
  await scope.loadContext({ id: 1 })
  expect(scope.date_from).toBe('2000-01-01')
  api.bossContext.mockResolvedValue({ business_today: '2024-03-02', warehouse_options: [], owner_options: [] })
  await scope.loadContext({ id: 1 }, true)
  expect(scope.businessToday).toBe('2024-03-02')
  expect(scope.date_to).toBe('2023-12-31')
  expect(scope.setDates('2024-03-02', '2024-03-02')).toBe(true)
})

it('starts with all owners in the warehouse even with a legacy saved owner filter', async () => {
  storage.set('boss_scope:1', JSON.stringify({ warehouse: '11', owner: '31', date_from: '2000-01-01', date_to: '2023-12-31', granularity: 'year' }))
  api.bossContext.mockResolvedValue({ business_today: '2024-03-01', warehouse_options: [{ id: 11 }], owner_options: [{ id: 31 }, { id: 32 }] })
  const scope = useBossScope()
  await scope.loadContext({ id: 1 })
  expect(scope.params.owner).toBeUndefined()
  expect(scope.warehouse).toBe('11')
  expect(scope.date_from).toBe('2000-01-01')
  expect(scope.granularity).toBe('year')

  scope.selectOwner('31')
  await scope.loadContext({ id: 1 }, true)
  expect(scope.params.owner).toBe('31')
  expect(JSON.parse(storage.get('boss_scope:1')).owner).toBeUndefined()

  setActivePinia(createPinia())
  const restarted = useBossScope()
  await restarted.loadContext({ id: 1 })
  expect(restarted.params.owner).toBeUndefined()
  expect(restarted.warehouse).toBe('11')
})

it('resets owner scope when switching accounts, while allowing selection of all owners', () => {
  storage.set('boss_scope:2', JSON.stringify({ owner: '32' }))
  const scope = useBossScope()
  scope.restore({ id: 1 })
  scope.selectOwner('31')
  scope.restore({ id: 1 })
  expect(scope.owner).toBe('31')
  scope.selectOwner('')
  expect(scope.params.owner).toBeUndefined()
  scope.selectOwner('31')
  scope.restore({ id: 2 })
  expect(scope.params.owner).toBeUndefined()
})

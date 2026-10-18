import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useBossScope } from '../store/bossScope'
import { api } from '../utils/request'

vi.mock('../utils/request', () => ({ api: { bossContext: vi.fn() } }))

describe('scope context recovery', () => {
  afterEach(() => vi.unstubAllGlobals())
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('uni', { getStorageSync: () => '', setStorageSync: vi.fn() })
  })

  it('rolls back warehouse and owner when context loading fails', async () => {
    const scope = useBossScope()
    scope.restore({ id: 1 })
    scope.$patch({ warehouse: '11', owner: '31', loaded: true,
      warehouseOptions: [{ id: 11, name: 'A' }], ownerOptions: [{ id: 31, name: 'Owner' }] })
    api.bossContext.mockRejectedValueOnce({ statusCode: 503 })
    await expect(scope.selectWarehouse('22')).rejects.toMatchObject({ statusCode: 503 })
    expect(scope.warehouse).toBe('11')
    expect(scope.owner).toBe('31')
    expect(scope.loaded).toBe(true)
    expect(scope.ownerOptions).toEqual([{ id: 31, name: 'Owner' }])
    expect(uni.setStorageSync).not.toHaveBeenCalled()
  })

  it('ignores a late context response from an earlier warehouse', async () => {
    const scope = useBossScope()
    scope.restore({ id: 1 })
    let finishOld
    api.bossContext.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
    api.bossContext.mockResolvedValueOnce({ warehouse_options: [{ id: 22 }], owner_options: [{ id: 42 }] })
    const old = scope.selectWarehouse('11')
    await scope.selectWarehouse('22')
    finishOld({ warehouse_options: [{ id: 11 }], owner_options: [{ id: 31 }] })
    await old
    expect(scope.warehouse).toBe('22')
    expect(scope.ownerOptions).toEqual([{ id: 42 }])
  })

  it('keeps the displayed selection until the new warehouse context succeeds', async () => {
    const scope = useBossScope()
    scope.restore({ id: 1 })
    scope.$patch({ warehouse: '11', owner: '31', loaded: true })
    let finish
    api.bossContext.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const selecting = scope.selectWarehouse('22')
    expect(api.bossContext).toHaveBeenLastCalledWith({ warehouse: '22' })
    expect(scope.warehouse).toBe('11')
    expect(scope.owner).toBe('31')
    finish({ warehouse_options: [{ id: 11 }, { id: 22 }], owner_options: [{ id: 42 }] })
    await selecting
    expect(scope.warehouse).toBe('22')
    expect(scope.owner).toBe('')
    expect(scope.loaded).toBe(true)
  })

  it('ignores an old failed context request after a newer selection succeeds', async () => {
    const scope = useBossScope()
    let rejectOld
    api.bossContext.mockImplementationOnce(() => new Promise((resolve, reject) => { rejectOld = reject }))
    const old = scope.loadContext({ id: 1 }, true)
    api.bossContext.mockResolvedValueOnce({ warehouse_options: [{ id: 22 }], owner_options: [] })
    await scope.selectWarehouse('22')
    rejectOld({ statusCode: 503 })
    await expect(old).resolves.toBe(false)
    expect(scope.warehouse).toBe('22')
    expect(scope.loaded).toBe(true)
  })

  it('does not restore a previous account scope when its context fails late', async () => {
    const scope = useBossScope()
    scope.restore({ id: 1 })
    scope.warehouse = '11'
    let rejectOld
    api.bossContext.mockImplementationOnce(() => new Promise((resolve, reject) => { rejectOld = reject }))
    const old = scope.selectWarehouse('22')
    scope.restore({ id: 2 })
    rejectOld({ statusCode: 403 })
    await old
    expect(scope.userId).toBe('2')
    expect(scope.warehouse).toBe('')
    expect(scope.ownerOptions).toEqual([])
  })
})

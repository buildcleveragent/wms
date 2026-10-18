import { beforeEach, describe, expect, it, vi } from 'vitest'
import { captureSession, clearPending, definitelyRejected, isCurrentSession, operationKey, readPending, writePending } from '@/utils/pendingOperation'
import { setToken } from '@/utils/request'

const auth = { profileLoaded: true, user: { id: 1, warehouse_id: 2 } }
let storage
beforeEach(() => {
  storage = new Map()
  vi.stubGlobal('uni', {
    getStorageSync: (key) => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, JSON.parse(JSON.stringify(value))),
    removeStorageSync: (key) => storage.delete(key),
  })
})

describe('durable pending operations', () => {
  it('isolates accounts, warehouses, tasks and operation kinds', () => {
    const keys = [operationKey(auth, 'pick', 1), operationKey(auth, 'pick', 2), operationKey(auth, 'return', 1), operationKey({ ...auth, user: { id: 2, warehouse_id: 2 } }, 'pick', 1), operationKey({ ...auth, user: { id: 1, warehouse_id: 3 } }, 'pick', 1)]
    expect(new Set(keys).size).toBe(5)
    expect(operationKey({ ...auth, profileLoaded: false }, 'pick', 1)).toBe('')
  })
  it('preserves the original payload across mutation, restart and retries', () => {
    const key = operationKey(auth, 'return')
    const payload = { sale_id: 4, idempotency_key: 'fixed-key', lines: [{ qty: '1.000' }] }
    writePending(key, payload)
    payload.lines[0].qty = '2.000'
    const recovered = readPending(key)
    expect(recovered.payload.lines[0].qty).toBe('1.000')
    recovered.payload.lines[0].qty = '3.000'
    expect(readPending(key).payload.lines[0].qty).toBe('1.000')
    clearPending(key)
    expect(readPending(key)).toBeNull()
  })
  it('does not proceed without persistent storage or a verified identity', () => {
    expect(() => writePending('', {})).toThrow(/登录/)
    uni.setStorageSync = () => { throw new Error('full') }
    expect(() => writePending(operationKey(auth, 'pick', 1), {})).toThrow(/存储/)
  })
  it('rejects late results after logout and re-login, even to the same account', () => {
    const captured = captureSession(auth)
    expect(isCurrentSession(auth, captured)).toBe(true)
    setToken('new-session')
    expect(isCurrentSession(auth, captured)).toBe(false)
  })
  it.each([401, 403, 404, 409, 500, 0])('keeps uncertain requests after status %s', (statusCode) => {
    expect(definitelyRejected({ statusCode })).toBe(false)
  })
  it('only clears an explicit validation rejection in the same session', () => {
    expect(definitelyRejected({ statusCode: 400 })).toBe(true)
    expect(definitelyRejected({ statusCode: 400, sessionChanged: true })).toBe(false)
  })
})

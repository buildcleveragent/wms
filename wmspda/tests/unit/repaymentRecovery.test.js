import { describe, expect, it, vi } from 'vitest'
import { getPendingRepayment, recordRepayment } from '@/utils/repaymentRecovery'

function setup() {
  const values = new Map()
  return {
    scope: 'warehouse1:cashier1',
    payload: { customer_id: 1, amount: '4.00', method: 'CASH' },
    storage: {
      getStorageSync: (key) => values.get(key),
      setStorageSync: (key, value) => values.set(key, structuredClone(value)),
      removeStorageSync: (key) => values.delete(key),
    },
    makeKey: vi.fn(() => 'stable-request-key'),
  }
}

describe('repayment recovery', () => {
  it('persists the original request across an uncertain response and restart', async () => {
    const args = setup()
    const send = vi.fn().mockRejectedValueOnce({ statusCode: 0 }).mockResolvedValueOnce({ id: 7 })
    await expect(recordRepayment({ ...args, send })).rejects.toMatchObject({ statusCode: 0 })
    expect(getPendingRepayment(args.scope, args.storage).payload).toEqual(args.payload)
    await expect(recordRepayment({ ...args, send })).resolves.toEqual({ id: 7 })
    expect(send.mock.calls[0][0]).toEqual(send.mock.calls[1][0])
    expect(args.makeKey).toHaveBeenCalledOnce()
    expect(getPendingRepayment(args.scope, args.storage)).toBeNull()
  })

  it('blocks changing an uncertain payment and isolates different cashiers', async () => {
    const args = setup()
    await recordRepayment({ ...args, send: () => Promise.reject({ statusCode: 503 }) }).catch(() => {})
    const send = vi.fn()
    await expect(recordRepayment({ ...args, payload: { ...args.payload, amount: '5.00' }, send }))
      .rejects.toThrow('尚有结果待确认')
    expect(send).not.toHaveBeenCalled()
    expect(getPendingRepayment('warehouse1:cashier2', args.storage)).toBeNull()
  })

  it('does not submit if durable storage fails', async () => {
    const args = setup()
    args.storage.setStorageSync = () => { throw new Error('storage full') }
    const send = vi.fn()
    await expect(recordRepayment({ ...args, send })).rejects.toThrow('storage full')
    expect(send).not.toHaveBeenCalled()
  })

  it('keeps an uncertain payment when an account switch discards its response', async () => {
    const args = setup()
    const send = vi.fn().mockRejectedValueOnce({ statusCode: 409, sessionChanged: true })
      .mockResolvedValueOnce({ id: 7 })
    await expect(recordRepayment({ ...args, send })).rejects.toMatchObject({ sessionChanged: true })
    expect(getPendingRepayment(args.scope, args.storage).idempotency_key).toBe('stable-request-key')
    await recordRepayment({ ...args, send })
    expect(send.mock.calls[1][0]).toEqual(send.mock.calls[0][0])
  })
})

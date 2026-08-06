import { describe, expect, it, vi } from 'vitest'
import { CHECKOUT_STATUS, executeCheckoutFlow, executePrintAttempt } from '@/utils/posCheckoutFlow'

describe('executeCheckoutFlow', () => {
  it('preserves the pending sale when checkout fails', async () => {
    const commit = vi.fn()
    const print = vi.fn()
    const error = new Error('timeout')
    const result = await executeCheckoutFlow({
      submit: vi.fn().mockRejectedValue(error), commit, print,
    })
    expect(result).toEqual({ status: CHECKOUT_STATUS.CHECKOUT_FAILED, error })
    expect(commit).not.toHaveBeenCalled()
    expect(print).not.toHaveBeenCalled()
  })

  it('commits before printing and isolates a false print result', async () => {
    const calls = []
    const result = await executeCheckoutFlow({
      submit: async () => ({ sale: { id: 1 } }),
      commit: async () => { calls.push('commit') },
      print: async () => { calls.push('print'); return false },
    })
    expect(result.status).toBe(CHECKOUT_STATUS.PRINT_FAILED)
    expect(calls).toEqual(['commit', 'print'])
  })

  it('commits before printing and isolates a thrown print error', async () => {
    const calls = []
    const result = await executeCheckoutFlow({
      submit: async () => ({ sale: { id: 1 } }),
      commit: async () => { calls.push('commit') },
      print: async () => { calls.push('print'); throw new Error('printer unavailable') },
    })
    expect(result.status).toBe(CHECKOUT_STATUS.PRINT_FAILED)
    expect(calls).toEqual(['commit', 'print'])
  })

  it('completes without printing when auto print is disabled', async () => {
    const print = vi.fn()
    const result = await executeCheckoutFlow({
      submit: async () => ({}), commit: async () => {}, print, shouldPrint: false,
    })
    expect(result.status).toBe(CHECKOUT_STATUS.COMPLETED)
    expect(print).not.toHaveBeenCalled()
  })

  it('classifies post-response UI failures as committed', async () => {
    const error = new Error('receipt rendering failed')
    const result = await executeCheckoutFlow({
      submit: async () => ({ sale: { id: 1 } }),
      commit: async () => { throw error },
      print: vi.fn(),
    })
    expect(result.status).toBe(CHECKOUT_STATUS.COMMITTED_UI_FAILED)
    expect(result.error).toBe(error)
  })

  it('reports manual reprint success and failure without throwing', async () => {
    await expect(executePrintAttempt(async () => true)).resolves.toEqual({
      status: CHECKOUT_STATUS.COMPLETED,
    })
    const failed = await executePrintAttempt(async () => { throw new Error('offline') })
    expect(failed.status).toBe(CHECKOUT_STATUS.PRINT_FAILED)
    expect(failed.error.message).toBe('offline')
  })
})

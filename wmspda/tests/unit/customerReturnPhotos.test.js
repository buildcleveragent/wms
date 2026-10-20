import { afterEach, beforeEach, expect, it, vi } from 'vitest'

let storage
beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  storage = { access: 'account-a', refresh: 'refresh-a' }
  vi.stubGlobal('uni', {
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    getStorageSync: key => storage[key] || '',
    setStorageSync: (key, value) => { storage[key] = value },
    removeStorageSync: key => { delete storage[key] },
    request: vi.fn(), showToast: vi.fn(), reLaunch: vi.fn(),
  })
  URL.createObjectURL = vi.fn(() => 'blob:private-return-photo')
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('reads private image bytes with authentication and releases the preview URL', async () => {
  uni.request.mockImplementation(opts => {
    expect(opts.header.Authorization).toBe('Bearer account-a')
    expect(opts.responseType).toBe('arraybuffer')
    opts.success({ statusCode: 200, data: new Uint8Array([255, 216]).buffer })
  })
  const { returnPhotoURL } = await import('@/utils/request')
  await expect(returnPhotoURL('/api/inbound/customer-returns/1/photos/2/'))
    .resolves.toBe('blob:private-return-photo')
  expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob))
  vi.advanceTimersByTime(60000)
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:private-return-photo')
})

it('does not create a preview from a response for the previous account', async () => {
  let finish
  uni.request.mockImplementation(opts => { finish = opts.success })
  const { returnPhotoURL, setAuthTokens } = await import('@/utils/request')
  const pending = returnPhotoURL('/api/inbound/customer-returns/1/photos/2/').catch(e => e)
  setAuthTokens({ access: 'account-b', refresh: 'refresh-b' })
  finish({ statusCode: 200, data: new ArrayBuffer(2) })
  expect((await pending).statusCode).toBe(409)
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})

it('does not preview forbidden photo responses', async () => {
  uni.request.mockImplementation(opts => opts.success({ statusCode: 403, data: {} }))
  const { returnPhotoURL } = await import('@/utils/request')
  await expect(returnPhotoURL('/api/inbound/customer-returns/1/photos/2/'))
    .rejects.toMatchObject({ statusCode: 403 })
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})

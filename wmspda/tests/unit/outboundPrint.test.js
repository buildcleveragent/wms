import { beforeEach, expect, it, vi } from 'vitest'
vi.mock('@/utils/request', () => ({ BASE_URL: 'http://localhost:8001', getToken: () => 'token', withAuthToken: vi.fn(async url => url + '&ticket=short-lived') }))
import { openOutboundPrintPage, prepareOutboundPrintWindow } from '@/utils/outboundPrint'
import { withAuthToken } from '@/utils/request'
beforeEach(() => { vi.clearAllMocks(); global.uni = { showToast: vi.fn() }; global.plus = { runtime: { openURL: vi.fn() } } })
it('preserves document kind and uses tickets', async () => {
  const handle = { document: { body: {} }, location: {}, close: vi.fn() }
  window.open = vi.fn(() => handle)
  const reserved = prepareOutboundPrintWindow()
  await openOutboundPrintPage(7, reserved, 'pick')
  expect(handle.location.href).toContain('/7/print/?kind=pick&ticket=')
  expect(window.open).toHaveBeenCalledTimes(1)
})
it('closes waiting window if ticket generation fails', async () => {
  const handle = { close: vi.fn() }
  withAuthToken.mockRejectedValueOnce(new Error('offline'))
  await openOutboundPrintPage(7, handle)
  expect(handle.close).toHaveBeenCalled()
  expect(uni.showToast).toHaveBeenCalledWith(expect.objectContaining({ title: expect.stringContaining('票据') }))
})
it('explains popup blocking without submitting any business action', async () => {
  window.open = vi.fn(() => null)
  await openOutboundPrintPage(7)
  expect(uni.showToast).toHaveBeenCalledWith(expect.objectContaining({ title: expect.stringContaining('阻止') }))
})

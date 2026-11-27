import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ReceiptPage from '@/pages/inbound/receive_task_detail.vue'

const hooks = vi.hoisted(() => ({}))
const api = vi.hoisted(() => ({
  inboundPdaTask: vi.fn(), inboundPdaLocations: vi.fn(), recordInboundReceipt: vi.fn(),
}))
vi.mock('@dcloudio/uni-app', () => ({
  onLoad: fn => { hooks.load = fn }, onHide: fn => { hooks.hide = fn },
}))
vi.mock('@/utils/useBarcodeScanner', () => ({
  useBarcodeScanner: ({ onScan }) => { hooks.scan = onScan },
}))
vi.mock('@/utils/request', () => ({ api, createIdempotencyUuid: () => crypto.randomUUID() }))

const serialSelector = 'input[placeholder="扫描或输入本件序列号"]'
let page, task

function card(index) {
  return page.findAll('.line-card')[index]
}

async function scan(value) {
  hooks.scan(value)
  await nextTick()
}

async function openPicker(index) {
  await card(index).get('.location-current button').trigger('click')
  await flushPromises()
}

beforeEach(async () => {
  vi.stubGlobal('uni', { showToast: vi.fn() })
  task = { id: 5, can_record: true, lines: [11, 12, 13].map(id => ({
    id, serial_control: id !== 13, qty_plan: 2, qty_done: 0, qty_pending: 2,
  })) }
  api.inboundPdaTask.mockResolvedValue(task)
  api.inboundPdaLocations.mockResolvedValue([{ id: 21, code: 'RCV-A' }])
  page = mount(ReceiptPage, { global: { config: { warnHandler: () => {} } } })
  hooks.load({ task_id: 5 })
  await flushPromises()
})

afterEach(() => { page.unmount(); vi.unstubAllGlobals() })

describe('physical scanner broadcast on the serial receipt page', () => {
  it('requires focus on the intended serial row and clears it on blur, hide and location selection', async () => {
    const first = card(0).get(serialSelector)
    const second = card(1).get(serialSelector)
    await scan('UNFOCUSED')
    expect(first.element.value).toBe('')
    await first.trigger('focus')
    await second.trigger('focus')
    await first.trigger('blur') // An old focus event may arrive after the new one.
    await scan('SN-B')
    expect(first.element.value).toBe('')
    expect(second.element.value).toBe('SN-B')
    await first.trigger('focus')
    await first.trigger('blur')
    await card(2).get('.quantity-grid input').trigger('focus')
    await scan('ORDINARY-ROW')
    expect(first.element.value).toBe('')
    await first.trigger('focus')
    hooks.hide()
    await scan('HIDDEN')
    expect(first.element.value).toBe('')
    await first.trigger('focus')
    await openPicker(0)
    await scan('PICKER')
    await page.get('.location-item').trigger('click')
    await scan('AFTER-PICKER')
    expect(first.element.value).toBe('')
    await first.trigger('tap')
    await scan('SN-A')
    expect(first.element.value).toBe('SN-A')
    expect(api.recordInboundReceipt).not.toHaveBeenCalled()
  })

  it('retains a typed value and protects a broadcast value and its request after a lost response', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    await openPicker(0)
    await page.get('.location-item').trigger('click')
    const serial = card(0).get(serialSelector)
    await serial.trigger('focus')
    await serial.trigger('input', { detail: { value: 'TYPED' } })
    await scan('OTHER')
    expect(serial.element.value).toBe('TYPED')
    await serial.trigger('input', { detail: { value: '' } })
    await scan(' SN-A ')
    await scan('SN-A')
    await scan('SN-B')
    expect(serial.element.value).toBe('SN-A')
    // A device may also send keyboard Enter for the same scan.
    await serial.trigger('confirm')
    expect(api.recordInboundReceipt).not.toHaveBeenCalled()
    let fail
    api.recordInboundReceipt.mockImplementationOnce(() => new Promise((_, reject) => { fail = reject }))
    await card(0).get('button.submit').trigger('click')
    await card(1).get(serialSelector).trigger('focus')
    await scan('WHILE-SAVING')
    expect(card(1).get(serialSelector).element.value).toBe('')
    fail({ statusCode: 0 })
    await flushPromises()
    expect(serial.element.disabled).toBe(true)
    expect(card(0).get('input[placeholder="可选"]').element.disabled).toBe(true)
    expect(card(0).text()).not.toContain('清除重扫')
    await serial.trigger('focus')
    await scan('UNKNOWN-REPLACEMENT')
    const original = api.recordInboundReceipt.mock.calls[0][1]
    const progressed = structuredClone(task)
    progressed.lines[0].qty_done = 1
    api.recordInboundReceipt.mockResolvedValueOnce({ idempotent: true, task: progressed })
    await card(0).get('button.submit').trigger('click')
    await flushPromises()
    expect(api.recordInboundReceipt.mock.calls[1][1]).toEqual(original)
    expect(serial.element.value).toBe('')
    await serial.trigger('focus')
    await scan('SN-B')
    api.recordInboundReceipt.mockResolvedValueOnce({ task: progressed })
    await card(0).get('button.submit').trigger('click')
    expect(api.recordInboundReceipt.mock.calls[2][1]).toMatchObject({
      serial_no: 'SN-B', qty_ok: 1, line_id: 11, finalize: true, location_id: 21,
    })
    expect(api.recordInboundReceipt.mock.calls[2][1].request_id).not.toBe(original.request_id)
  })

  it('lets an operator clear and rescan after a definitive server rejection', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    await openPicker(0)
    await page.get('.location-item').trigger('click')
    const serial = card(0).get(serialSelector)
    await serial.trigger('focus')
    await scan('ALREADY-RECEIVED')
    api.recordInboundReceipt.mockRejectedValueOnce({ statusCode: 400 })
    await card(0).get('button.submit').trigger('click')
    await flushPromises()
    await card(0).findAll('button').find(button => button.text() === '清除重扫').trigger('click')
    await scan('UNFOCUSED')
    expect(serial.element.value).toBe('')
    await serial.trigger('focus')
    await scan('NEW-SERIAL')
    expect(serial.element.value).toBe('NEW-SERIAL')
    expect(api.recordInboundReceipt).toHaveBeenCalledTimes(1)
  })
})

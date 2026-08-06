import { beforeEach, describe, expect, it, vi } from 'vitest'

const hooks = vi.hoisted(() => ({ show: [], hide: [], unload: [], unmounted: [] }))
vi.mock('@dcloudio/uni-app', () => ({
  onShow: (fn) => hooks.show.push(fn),
  onHide: (fn) => hooks.hide.push(fn),
  onUnload: (fn) => hooks.unload.push(fn),
}))
vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, onUnmounted: (fn) => hooks.unmounted.push(fn) }
})

import { useBarcodeScanner } from '@/utils/useBarcodeScanner'

function createNativeEnvironment({ unregisterThrows = false } = {}) {
  const receivers = new Set()
  const activity = {
    registerReceiver: vi.fn((receiver) => receivers.add(receiver)),
    unregisterReceiver: vi.fn((receiver) => {
      receivers.delete(receiver)
      if (unregisterThrows) throw new Error('native unregister failed')
    }),
  }
  globalThis.uni = {
    requireNativePlugin: vi.fn(() => ({ startScan: vi.fn() })),
    vibrateShort: vi.fn(),
    showToast: vi.fn(),
  }
  globalThis.plus = { android: {
    runtimeMainActivity: () => activity,
    importClass: vi.fn((name) => typeof name === 'string' && name.includes('IntentFilter')
      ? class { addAction() {} }
      : name),
    implements: vi.fn((_name, implementation) => implementation),
  } }
  return { activity, receivers }
}

function broadcast(receivers, code) {
  const intent = { getStringExtra: () => code }
  for (const receiver of [...receivers]) receiver.onReceive(null, intent)
}

describe('useBarcodeScanner lifecycle', () => {
  beforeEach(() => {
    for (const list of Object.values(hooks)) list.length = 0
    vi.clearAllMocks()
  })

  it('registers once, pauses on hide, and restores the callback on show', () => {
    const { activity, receivers } = createNativeEnvironment()
    const onScan = vi.fn()
    useBarcodeScanner({ onScan })
    hooks.show[0](); hooks.show[0]()
    expect(activity.registerReceiver).toHaveBeenCalledTimes(1)
    broadcast(receivers, 'A')
    expect(onScan).toHaveBeenCalledWith('A')
    hooks.hide[0]()
    broadcast(receivers, 'HIDDEN')
    expect(onScan).toHaveBeenCalledTimes(1)
    hooks.show[0]()
    broadcast(receivers, 'B')
    expect(onScan).toHaveBeenLastCalledWith('B')
  })

  it('allows only the visible page instance to consume a broadcast', () => {
    const { receivers } = createNativeEnvironment()
    const scanA = vi.fn(); const scanB = vi.fn()
    useBarcodeScanner({ onScan: scanA })
    useBarcodeScanner({ onScan: scanB })
    hooks.show[0](); hooks.hide[0](); hooks.show[1]()
    broadcast(receivers, 'ONLY-B')
    expect(scanA).not.toHaveBeenCalled()
    expect(scanB).toHaveBeenCalledOnce()
  })

  it('disposes idempotently and recovers state after unregister throws', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { activity } = createNativeEnvironment({ unregisterThrows: true })
    useBarcodeScanner({ onScan: vi.fn() })
    hooks.show[0](); hooks.hide[0](); hooks.show[0]()
    expect(activity.registerReceiver).toHaveBeenCalledTimes(2)
    hooks.unload[0](); hooks.unmounted[0]()
    hooks.show[0]()
    expect(activity.registerReceiver).toHaveBeenCalledTimes(2)
  })
})

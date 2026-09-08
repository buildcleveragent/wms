import fs from 'node:fs'
import path from 'node:path'
import { ref, computed } from 'vue'
import { describe, expect, it, vi } from 'vitest'

// Execute each page's actual setup handlers; browser tests separately cover rendering.
function page(kind, send) {
  const source = fs.readFileSync(path.join(process.env.WMS_SCAN_SOURCE_ROOT || path.resolve(import.meta.dirname, '../..'), `pages/inventory/${kind}/detail.vue`), 'utf8')
  const script = source.match(/<script setup>([\s\S]*?)<\/script>/)[1]
    .replace(/^import[^\n]*\n/gm, '')
  let sequence = 0
  const api = { recordRelocation: send, recordReplenishment: send }
  const init = new Function('ref', 'computed', 'onLoad', 'useAuth', 'api', 'createIdempotencyUuid', 'uni',
    `${script}; return { hydrate, record, forms, task, saving };`)
  const value = init(ref, computed, () => {}, () => ({}), api, () => `scan-${++sequence}`,
    { showToast: vi.fn() })
  value.hydrate({ lines: [{ id: 1, qty_pending: 10 }] })
  Object.assign(value.forms.value[1], { from: 'SOURCE', to: 'TARGET', product: 'SKU', qty: 4 })
  return value
}

describe.each(['move', 'replenish'])('%s scan operation lifecycle', kind => {
  it('records 4 + 4 + 2 as three distinct operations', async () => {
    const keys = new Map()
    let done = 0
    const send = vi.fn(async (_, payload) => {
      if (!keys.has(payload.request_id)) { done += payload.qty; keys.set(payload.request_id, payload) }
      return { task: { lines: [{ id: 1, qty_pending: 10 - done, qty_done: done }] } }
    })
    const value = page(kind, send)
    for (const [qty, expected] of [[4, 4], [4, 8], [2, 10]]) {
      value.forms.value[1].qty = qty
      await value.record({ id: 1 })
      expect(value.task.value.lines[0].qty_done).toBe(expected)
    }
    expect(keys.size).toBe(3)
  })

  it('keeps the exact uncertain operation, blocks edited retry, then rotates after confirmation', async () => {
    const send = vi.fn().mockRejectedValueOnce(new Error('response lost'))
      .mockResolvedValue({ idempotent: true, task: { lines: [{ id: 1, qty_pending: 6, qty_done: 4 }] } })
    const value = page(kind, send)
    await value.record({ id: 1 }).catch(() => {})
    const original = structuredClone(send.mock.calls[0][1])
    value.forms.value[1].qty = 2
    await value.record({ id: 1 }).catch(() => {})
    expect(send).toHaveBeenCalledTimes(1)
    value.forms.value[1].qty = 4
    await value.record({ id: 1 })
    expect(send.mock.calls[1][1]).toEqual(original)
    expect(value.forms.value[1].requestId).not.toBe(original.request_id)
  })

  it('ignores double clicks while a scan is pending', async () => {
    let finish
    const send = vi.fn(() => new Promise(resolve => { finish = resolve }))
    const value = page(kind, send)
    const first = value.record({ id: 1 })
    const second = value.record({ id: 1 })
    expect(send).toHaveBeenCalledTimes(1)
    finish({ task: { lines: [{ id: 1, qty_pending: 6 }] } })
    await Promise.all([first, second])
  })
})

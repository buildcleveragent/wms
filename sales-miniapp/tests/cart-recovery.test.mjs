import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import test from 'node:test'

const source = (await fs.readFile(new URL('../stores/cart.js', import.meta.url), 'utf8'))
  .replace("import { reactive } from 'vue'", 'const reactive = value => value')
  .replace("import { cartService } from '../services/cart'", 'const cartService = globalThis.syntheticCartService')
let sequence = 0

async function setup(service) {
  globalThis.syntheticCartService = service
  globalThis.uni = { getStorageSync: () => null, setStorageSync() {} }
  const module = await import(`data:text/javascript;base64,${Buffer.from(source + `\n// ${sequence++}`).toString('base64')}`)
  return module.useCartStore()
}

test('uncertain add reads current cart without resending the cumulative operation', async () => {
  let writes = 0, reads = 0
  const cart = await setup({ add: async () => { writes++; throw { statusCode: 0 } },
    get: async () => { reads++; return { items: [] } } })
  await assert.rejects(cart.addProduct({ id: 1 }, 1), /结果尚未确认，已刷新购物车/)
  assert.equal(writes, 1)
  assert.equal(reads, 1)
  assert.equal(cart.synced, true)
})

test('confirmed add and failed refresh do not mislabel the write as failed', async () => {
  let writes = 0
  const cart = await setup({ add: async () => { writes++; return { ok: true } },
    get: async () => { throw new Error('offline') } })
  await assert.rejects(cart.addProduct({ id: 1 }, 1), /已加购，购物车刷新失败/)
  assert.equal(writes, 1)
  assert.equal(cart.synced, false)
})

test('business 400 preserves its diagnosis and is never retried', async () => {
  let writes = 0, reads = 0
  const error = { statusCode: 400, message: '包装数量不符合规则' }
  const cart = await setup({ add: async () => { writes++; throw error }, get: async () => { reads++ } })
  await assert.rejects(cart.addProduct({ id: 1 }, 1), actual => actual === error)
  assert.equal(writes, 1)
  assert.equal(reads, 0)
})

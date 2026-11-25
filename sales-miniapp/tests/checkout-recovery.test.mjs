import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import test from 'node:test'

const source = (await fs.readFile(new URL('../services/checkout.js', import.meta.url), 'utf8'))
  .replace("import { getBaseUrl, getToken } from '../utils/request'", 'const getBaseUrl = () => globalThis.base; const getToken = () => uni.getStorageSync("token")')
  .replace("import { orderService } from './order'", 'const orderService = globalThis.orders')
let revision = 0
async function load() {
  return import(`data:text/javascript;base64,${Buffer.from(source + '\n// ' + revision++).toString('base64')}`)
}
function setup() {
  const storage = { token: 'test', sales_user: { id: 1 } }
  globalThis.base = 'https://mall.example.test'
  globalThis.uni = {
    getStorageSync: key => storage[key],
    setStorageSync: (key, value) => { storage[key] = structuredClone(value) },
    removeStorageSync: key => { delete storage[key] },
  }
  return storage
}
const prepare = async () => ({ lines: [{ product_id: 123, qty: '2' }], contact: 'synthetic' })
const noCart = async () => { throw new Error('cart already empty') }

test('lost response and app restart use the original key and snapshot before cart validation', async () => {
  const storage = setup()
  const calls = []
  globalThis.orders = {
    async create(payload, key) {
      calls.push({ payload, key })
      if (calls.length === 1) throw { statusCode: 0 }
      return { id: 77, orders: [{ id: 77 }, { id: 78 }] }
    },
    async detail(id) { assert.equal(id, 77); return { id, payment_status: 'PAID' } },
  }
  let client = await load()
  await assert.rejects(client.submitCheckout(prepare))
  client = await load()
  storage.sales_user = { id: 2 }
  assert.equal(client.pendingCheckout(), null)
  storage.sales_user = { id: 1 }
  globalThis.base = 'https://other.example.test'
  assert.equal(client.pendingCheckout(), null)
  globalThis.base = 'https://mall.example.test'
  const recovered = await client.submitCheckout(noCart)
  assert.deepEqual(calls[0], calls[1])
  assert.equal(recovered.order.orders.length, 2)
  assert.equal(recovered.recovering, true)
  assert.equal(client.pendingCheckout().payload, undefined)
  assert.equal((await client.submitCheckout(noCart)).order.payment_status, 'PAID')
  assert.equal(calls.length, 2)
  client.finishCheckout(recovered)
  const next = await client.submitCheckout(prepare)
  assert.notEqual(next.requestKey, recovered.requestKey)
  assert.equal(next.recovering, false)
})

test('storage failure prevents submitting and business rejection permits editing the checkout', async () => {
  setup()
  let calls = 0
  globalThis.orders = { create() { calls++; throw { statusCode: 400 } } }
  const client = await load()
  const save = uni.setStorageSync
  uni.setStorageSync = () => { throw new Error('storage full') }
  await assert.rejects(client.submitCheckout(prepare), /storage full/)
  assert.equal(calls, 0)
  uni.setStorageSync = save
  await assert.rejects(client.submitCheckout(prepare))
  assert.equal(calls, 1)
  assert.equal(client.pendingCheckout(), null)
})

test('ambiguous errors and conflicts retain original intent without issuing another key', async () => {
  setup()
  let code = 503
  globalThis.orders = { create() { throw { statusCode: code } } }
  const client = await load()
  await assert.rejects(client.submitCheckout(prepare))
  const key = client.pendingCheckout().requestKey
  for (code of [401, 403, 409]) {
    await assert.rejects(client.submitCheckout(noCart))
    assert.equal(client.pendingCheckout().requestKey, key)
  }
})

test('a known order response and delayed cleanup cannot cross accounts with the same key', async () => {
  const storage = setup()
  let finishDetail
  globalThis.orders = {
    async create() { return { id: 7 } },
    detail() { return new Promise(resolve => { finishDetail = resolve }) },
  }
  const client = await load()
  const original = await client.submitCheckout(prepare)
  const lookup = client.submitCheckout(noCart)
  storage.sales_user = { id: 2 }
  const otherScope = original.scope.replace(/:1$/, ':2')
  storage[otherScope] = { requestKey: original.requestKey, orderId: 8 }
  finishDetail({ id: 7 })
  await assert.rejects(lookup, /切回原账号/)
  assert.equal(client.checkoutIsCurrent(original), false)
  client.finishCheckout(original)
  assert.equal(client.pendingCheckout().orderId, 8)
  storage.sales_user = { id: 1 }
  assert.equal(client.pendingCheckout().orderId, 7)
  globalThis.base = 'https://other.example.test'
  assert.equal(client.checkoutIsCurrent(original), false)
})

test('a delayed POST receipt cannot replace a newer checkout on the same account', async () => {
  setup()
  let resolveOldPost, started
  const sent = new Promise(resolve => { started = resolve })
  let calls = 0
  globalThis.orders = {
    create() {
      calls++
      if (calls === 1) {
        started()
        return new Promise(resolve => { resolveOldPost = resolve })
      }
      return { id: calls === 2 ? 71 : 82 }
    },
  }
  const client = await load()
  const oldPost = client.submitCheckout(prepare)
  await sent
  const originalReplay = await client.submitCheckout(noCart)
  client.finishCheckout(originalReplay)
  const newCheckout = await client.submitCheckout(prepare)
  assert.notEqual(originalReplay.requestKey, newCheckout.requestKey)
  resolveOldPost({ id: 71 })
  await assert.rejects(oldPost, /结算已变化/)
  assert.equal(client.pendingCheckout().requestKey, newCheckout.requestKey)
  assert.equal(client.pendingCheckout().orderId, 82)
})

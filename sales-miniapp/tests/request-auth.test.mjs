import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import test from 'node:test'

const source = (await fs.readFile(new URL('../utils/request.js', import.meta.url), 'utf8'))
  .replace("import { BASE_URL } from './api-base'", "const BASE_URL = 'https://example.test'")
let serial = 0

async function setup(respond) {
  const storage = { sales_access: 'old', sales_refresh: 'refresh' }
  const calls = []
  let redirects = 0
  globalThis.uni = {
    getStorageSync: (key) => storage[key] || '',
    setStorageSync: (key, value) => { storage[key] = value },
    removeStorageSync: (key) => { delete storage[key] },
    showToast() {}, reLaunch() { redirects++ },
    request(options) { calls.push(options); respond(options) },
    uploadFile(options) { calls.push(options); respond(options) },
  }
  const api = await import(`data:text/javascript;base64,${Buffer.from(source + `\n// run ${serial++}`).toString('base64')}`)
  return { api, storage, calls, redirects: () => redirects }
}

test('mall refresh shares one request and business retry errors retain the session', async () => {
  let finish
  let refreshes = 0
  const { api, storage, redirects } = await setup((options) => {
    if (options.url.endsWith('/refresh/')) { refreshes++; finish = options.success }
    else options.success({ statusCode: options.header.Authorization === 'Bearer old' ? 401 : 400, data: {} })
  })
  const pending = Array.from({ length: 5 }, () => api.request({ url: '/orders/' }).catch(error => error))
  await new Promise(resolve => setImmediate(resolve))
  finish({ statusCode: 200, data: { access: 'new', refresh: 'rotated' } })
  const results = await Promise.all(pending)
  assert.equal(refreshes, 1)
  assert.ok(results.every(error => error.statusCode === 400))
  assert.equal(storage.sales_access, 'new')
  assert.equal(storage.sales_refresh, 'rotated')
  assert.equal(redirects(), 0)
})

test('mall upload retries once with the refreshed bearer token', async () => {
  const { api, storage } = await setup(options => {
    if (options.url.endsWith('/refresh/')) options.success({ statusCode: 200, data: { access: 'new' } })
    else options.success({ statusCode: options.header.Authorization === 'Bearer old' ? 401 : 200, data: '{"ok":true}' })
  })
  assert.deepEqual(await api.uploadFile({ url: '/upload/', filePath: '/tmp/test.png' }), { ok: true })
  assert.equal(storage.sales_access, 'new')
})

test('offline refresh preserves credentials; rejected refresh expires them', async () => {
  for (const offline of [true, false]) {
    const { api, storage, redirects } = await setup(options => {
      if (options.url.endsWith('/refresh/') && offline) options.fail({ errMsg: 'offline' })
      else options.success({ statusCode: 401, data: {} })
    })
    await assert.rejects(api.request({ url: '/orders/' }))
    assert.equal(storage.sales_access, offline ? 'old' : undefined)
    assert.equal(redirects(), offline ? 0 : 1)
  }
})

test('late refresh rejection cannot clear a newly logged-in account', async () => {
  let finish
  const { api, storage, calls, redirects } = await setup(options => {
    if (options.url.endsWith('/refresh/')) finish = options.success
    else options.success({ statusCode: 401, data: {} })
  })
  const pending = api.request({ url: '/orders/', method: 'POST' }).catch(error => error)
  await new Promise(resolve => setImmediate(resolve))
  api.setToken('account-b-access', 'account-b-refresh')
  finish({ statusCode: 401, data: {} })
  await pending
  assert.equal(storage.sales_access, 'account-b-access')
  assert.equal(storage.sales_refresh, 'account-b-refresh')
  assert.equal(calls.length, 2)
  assert.equal(redirects(), 0)
})

test('old request and upload responses cannot cross an account switch', async () => {
  for (const upload of [false, true]) {
    for (const statusCode of [200, 401]) {
      let finish
      const { api, storage, calls } = await setup(options => { finish = options.success })
      const request = upload
        ? api.uploadFile({ url: '/upload/', filePath: '/tmp/test.png' })
        : api.request({ url: '/orders/', method: 'POST' })
      const pending = request.catch(error => error)
      api.setToken('account-b-access', 'account-b-refresh')
      finish({ statusCode, data: {} })
      assert.equal((await pending).statusCode, 409)
      assert.equal(calls.length, 1)
      assert.equal(storage.sales_access, 'account-b-access')
    }
  }
})

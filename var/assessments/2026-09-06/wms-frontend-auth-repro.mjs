// Offline reproduction: no network, real credentials, browser, or database is used.
// Run from /wms: node /tmp/wms-frontend-auth-repro.mjs
import fs from 'node:fs'
import path from 'node:path'

const root = '/wms'
const output = []
async function scenario(client, failure) {
  const removed = [], notifications = [], requests = [], cart = []
  const storage = { access: 'test-old-access', refresh: 'test-refresh', user: 'test-user' }
  globalThis.uni = {
    getStorageSync: (key) => storage[key],
    setStorageSync: (key, value) => { storage[key] = value },
    removeStorageSync: (key) => { removed.push(key); delete storage[key] },
    showToast: (options) => notifications.push(options.title),
    request: (options) => {
      requests.push(options.url)
      if (requests.length === 1) options.success({ statusCode: 401, data: { detail: 'expired' } })
      else if (requests.length === 2 && failure === 'network') options.fail({ errMsg: 'offline' })
      else if (requests.length === 2) options.success({ statusCode: 200, data: { access: 'test-new-access', refresh: 'test-new-refresh' } })
      else options.success({ statusCode: 400, data: { detail: 'business validation failed' } })
    },
  }
  globalThis.setTimeout = () => 0
  globalThis.__reviewCartReset = () => cart.push('reset')
  const source = fs.readFileSync(path.join(root, client, 'utils/request.js'), 'utf8')
    .replace(/^export \{ BASE_URL \} from '.\/api-base'\n/m, '')
    .replace(/^import \{ BASE_URL \} from '.\/api-base'\n/m, "const BASE_URL = 'https://example.invalid'\n")
    .replace(/^import \{ useCart \} from '@\/store\/cart'\n/m, 'const useCart = () => ({resetOrder: globalThis.__reviewCartReset})\n')
  const module = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${client}-${failure}`)
  try {
    await module.request({ url: '/review-only-mocked-endpoint/' })
  } catch (error) {
    const result = { client, failure, returnedStatus: error.statusCode, requests: requests.length, removed, cart, notifications }
    output.push(result)
    console.log(JSON.stringify(result))
  }
}

await scenario('wmsbossbilling', 'network')
await scenario('wmsbossbilling', 'business')
await scenario('wmsownersale', 'business')
fs.writeFileSync('/tmp/wms-frontend-auth-repro-output.json', JSON.stringify(output, null, 2) + '\n')

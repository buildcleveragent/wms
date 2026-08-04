import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const read = (name) => fs.readFileSync(path.join(root, name), 'utf8')
const stripJsonComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1')
const pagesManifest = JSON.parse(stripJsonComments(read('pages.json')))
const manifest = JSON.parse(stripJsonComments(read('manifest.json')))
const registered = new Set(pagesManifest.pages.map((entry) => entry.path))

function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(directory, entry.name)
    return entry.isDirectory() ? walk(absolute) : [absolute]
  })
}

const pageFiles = walk(path.join(root, 'pages'))
  .filter((file) => /\.(vue|nvue)$/.test(file))
  .map((file) => path.relative(root, file).replace(/\\/g, '/').replace(/\.(vue|nvue)$/, ''))

assert.deepEqual([...pageFiles].sort(), [...registered].sort(), 'every page component must be registered exactly once')
for (const route of registered) assert.ok(fs.existsSync(path.join(root, `${route}.vue`)), `missing ${route}.vue`)
assert.equal(pagesManifest.pages[0].path, 'pages/bootstrap', 'bootstrap must be the cold-start page')

const permissions = manifest['app-plus'].distribute.android.permissions
assert.deepEqual(permissions, [
  '<uses-permission android:name="android.permission.VIBRATE"/>',
  '<uses-permission android:name="android.permission.CAMERA"/>',
  '<uses-feature android:name="android.hardware.camera" android:required="false"/>',
  '<uses-feature android:name="android.hardware.camera.autofocus" android:required="false"/>',
])

const source = walk(root)
  .filter((file) => /\.(js|vue|nvue|json|mjs)$/.test(file)
    && !file.includes('node_modules')
    && !file.includes(`${path.sep}unpackage${path.sep}`)
    && !file.includes(`${path.sep}scripts${path.sep}`))
  .map((file) => fs.readFileSync(file, 'utf8'))
  .join('\n')
for (const forbidden of ['window.open(', 'plus.runtime.openURL(', 'READ_LOGS', 'READ_PHONE_STATE', 'WRITE_SETTINGS']) {
  assert.ok(!source.includes(forbidden), `forbidden legacy capability: ${forbidden}`)
}
for (const pathname of pageFiles) {
  assert.ok(!/(backup|副本|\/sample\/)/i.test(pathname), `legacy page remains: ${pathname}`)
}

const requestSource = read('utils/request.js')
assert.match(requestSource, /let refreshPromise = null/)
assert.match(requestSource, /ROTATE|data\.refresh \|\| refresh/)
assert.match(requestSource, /Authorization: `Bearer \$\{access\}`/)
assert.match(requestSource, /responseType: 'arraybuffer'/)
const productSearch = read('pages/products/search.vue')
assert.match(productSearch, /@scrolltolower="loadProducts"/)
assert.doesNotMatch(productSearch, /onReachBottom/)
assert.match(read('pages/orders/index.vue'), /onReachBottom/)
assert.ok(!registered.has('pages/workbench/index'), 'legacy workbench route must be removed')
assert.match(requestSource, /uploadAuthenticatedFile/)

console.log(`owner client contract OK: ${registered.size} registered pages`)

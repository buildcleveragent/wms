import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadEnv } from 'vite'
import { validateBossApiBase } from '../utils/api-base-policy.mjs'
import { renderAndroidNetworkSecurityConfig } from '../config/androidNetworkSecurity.mjs'
import { ANDROID_PACKAGE_NAME, DCLOUD_APP_ID } from '../config/androidReleaseIdentity.mjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const env = loadEnv('production', path.join(root, 'config/android'), '')
const options = {
  value: env.VITE_API_BASE_URL, platform: 'app', mode: 'production',
  allowHttp: env.VITE_ALLOW_HTTP_API === 'true',
  allowPrivate: env.VITE_ALLOW_PRIVATE_API === 'true',
}
const base = validateBossApiBase(options)
const read = name => fs.readFileSync(path.join(root, 'dist/build/app', name), 'utf8')
const manifest = JSON.parse(read('manifest.json'))
const source = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'))
const sourceAndroid = source['app-plus'].distribute.android
const builtAndroid = manifest.plus.distribute.google
const icons = source['app-plus'].distribute.icons.android
const expectedIcons = { hdpi: 72, xhdpi: 96, xxhdpi: 144, xxxhdpi: 192 }

function pngDimensions(filename) {
  const png = fs.readFileSync(filename)
  assert.equal(png.subarray(1, 4).toString(), 'PNG')
  return [png.readUInt32BE(16), png.readUInt32BE(20)]
}

assert.equal(source.name, '仓储经营分析中心')
assert.equal(source.appid, DCLOUD_APP_ID)
assert.equal(sourceAndroid.packagename, ANDROID_PACKAGE_NAME)
assert.equal(sourceAndroid.minSdkVersion, 24)
assert.deepEqual(source['app-plus'].orientation, ['portrait-primary'])
assert.ok(sourceAndroid.permissions.includes(
  '<uses-permission android:name="android.permission.INTERNET"/>',
))
assert.ok(sourceAndroid.permissions.includes(
  '<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/>',
))
assert.equal(manifest.id, source.appid)
assert.equal(manifest.name, source.name)
assert.equal(manifest.version.name, source.versionName)
assert.equal(String(manifest.version.code), String(source.versionCode))
assert.equal(builtAndroid.packagename, ANDROID_PACKAGE_NAME)
assert.equal(builtAndroid.minSdkVersion, 24)
assert.deepEqual(manifest.plus.orientation, ['portrait-primary'])
assert.deepEqual(manifest.plus.distribute.icons.android, icons)
assert.deepEqual(builtAndroid.permissions, sourceAndroid.permissions)
assert.equal(builtAndroid.networkSecurityConfig, 'xml/network_security_config.xml')
for (const [density, size] of Object.entries(expectedIcons)) {
  assert.equal(icons[density], `static/app-icons/icon-${size}.png`)
  assert.deepEqual(pngDimensions(path.join(root, icons[density])), [size, size])
  assert.deepEqual(pngDimensions(path.join(root, 'dist/build/app', icons[density])), [size, size])
}
assert.ok(
  read('app-service.js').includes(base),
  'Native runtime must embed the configured API origin',
)
const nativeManifest = read('AndroidManifest.xml')
assert.ok(nativeManifest.includes(`package="${ANDROID_PACKAGE_NAME}"`))
assert.equal(nativeManifest, fs.readFileSync(path.join(root, 'AndroidManifest.xml'), 'utf8'))
assert.equal(
  read('nativeResources/android/res/xml/network_security_config.xml'),
  renderAndroidNetworkSecurityConfig(options),
)
console.log('Android runtime, manifest and API configuration verified. Output is not a signed APK.')

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadEnv } from 'vite'
import { validateOwnerApiBase } from '../utils/api-base-policy.js'
import { renderAndroidNetworkSecurityConfig } from '../config/androidNetworkSecurity.mjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const env = loadEnv('production', path.join(root, 'config/android'), '')
const options = {
  value: env.VITE_API_BASE_URL, platform: 'app', appPlatform: 'android', mode: 'production',
  allowHttp: env.VITE_ALLOW_HTTP_API === 'true', allowPrivate: env.VITE_ALLOW_PRIVATE_API === 'true',
}
const base = validateOwnerApiBase(options)
const output = path.join(root, 'dist/build/app')
const read = name => fs.readFileSync(path.join(output, name), 'utf8')
const manifest = JSON.parse(read('manifest.json'))
const sourceManifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'))
assert.equal(manifest.id, sourceManifest.appid)
assert.equal(manifest.version.name, sourceManifest.versionName)
assert.equal(String(manifest.version.code), String(sourceManifest.versionCode))
assert.equal(manifest.plus.distribute.google.packagename, 'com.jinqiaorongtong.wmsownersale')
assert.equal(manifest.plus.distribute.google.networkSecurityConfig, 'xml/network_security_config.xml')
assert.ok(manifest.permissions.Barcode, 'App must include its existing scan capability')
assert.ok(manifest.plus.distribute.google.permissions.every(entry => !/<uses-feature\b/.test(entry)),
  'Camera features must not duplicate cloud-generated Barcode declarations in includePermissions')
assert.ok(read('app-service.js').includes(base), 'App resources must embed the configured public API origin')
assert.equal(read('AndroidManifest.xml'), fs.readFileSync(path.join(root, 'AndroidManifest.xml'), 'utf8'))
assert.equal(read('nativeResources/android/res/xml/network_security_config.xml'), renderAndroidNetworkSecurityConfig(options))
console.log(`Android resources verified: ${manifest.id}, ${manifest.version.name}, API ${base}. This is not a signed APK.`)

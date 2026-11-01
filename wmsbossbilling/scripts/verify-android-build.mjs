import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadEnv } from 'vite'
import { validateBossApiBase } from '../utils/api-base-policy.mjs'
import { renderAndroidNetworkSecurityConfig } from '../config/androidNetworkSecurity.mjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const env = loadEnv('production', path.join(root, 'config/android'), '')
const options = {
  value: env.VITE_API_BASE_URL, platform: 'app', mode: 'production',
  allowHttp: env.VITE_ALLOW_HTTP_API === 'true', allowPrivate: env.VITE_ALLOW_PRIVATE_API === 'true',
}
const base = validateBossApiBase(options)
const read = name => fs.readFileSync(path.join(root, 'dist/build/app', name), 'utf8')
const manifest = JSON.parse(read('manifest.json'))
const source = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'))
assert.equal(manifest.id, source.appid)
assert.equal(manifest.version.name, source.versionName)
assert.equal(String(manifest.version.code), String(source.versionCode))
assert.equal(manifest.plus.distribute.google.networkSecurityConfig, 'xml/network_security_config.xml')
assert.ok(read('app-service.js').includes(base), 'Native runtime must embed the configured API origin')
assert.equal(read('AndroidManifest.xml'), fs.readFileSync(path.join(root, 'AndroidManifest.xml'), 'utf8'))
assert.equal(read('nativeResources/android/res/xml/network_security_config.xml'), renderAndroidNetworkSecurityConfig(options))
console.log('Android runtime, manifest and API configuration verified. Output is not a signed APK.')

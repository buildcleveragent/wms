import fs from 'node:fs'
import vm from 'node:vm'
import { afterEach, expect, it, vi } from 'vitest'
import { validateOwnerApiBase } from '../utils/api-base-policy.js'
import { renderAndroidNetworkSecurityConfig } from '../config/androidNetworkSecurity.mjs'
import { validateApiBase } from '../../frontend/api-base-policy.mjs'

const android = { value: 'http://8.148.198.200:8080/', platform: 'app', appPlatform: 'android', mode: 'production', allowHttp: true }
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.resetModules() })

it('requires an explicit Android-only HTTP opt in and preserves the port', () => {
  expect(validateOwnerApiBase(android)).toBe('http://8.148.198.200:8080')
  expect(() => validateOwnerApiBase({ ...android, allowHttp: false })).toThrow('HTTPS')
  expect(() => validateOwnerApiBase({ ...android, appPlatform: 'ios' })).toThrow('HTTPS')
  for (const platform of ['h5', 'mp-weixin', 'app-harmony']) {
    expect(() => validateOwnerApiBase({ ...android, platform })).toThrow('HTTPS')
  }
  expect(() => validateApiBase(android)).toThrow('HTTPS') // Other clients keep their policy.
})

it.each(['http://8.148.198.200:8080/api', 'http://u:p@8.148.198.200:8080',
  'http://8.148.198.200:8080?x=1', 'http://8.148.198.200:8080/#/pages/login',
  'ftp://8.148.198.200', 'not a url', 'http://8.148.198.200:99999', ''])('rejects invalid app base %s', value => {
  expect(() => validateOwnerApiBase({ ...android, value })).toThrow()
})

it('keeps private hosts opt in and HTTPS available', () => {
  expect(() => validateOwnerApiBase({ ...android, value: 'http://192.168.1.6:8001' })).toThrow('Private')
  expect(validateOwnerApiBase({ ...android, value: 'https://wms.example.com', allowHttp: false })).toBe('https://wms.example.com')
  expect(validateOwnerApiBase({ value: '', platform: 'h5' })).toBe('')
  expect(validateOwnerApiBase({ value: 'http://localhost:8001', platform: 'h5', mode: 'development' })).toBe('http://localhost:8001')
})

it('limits Android cleartext to the configured host and removes it in HTTPS releases', () => {
  const xml = renderAndroidNetworkSecurityConfig(android)
  const parsed = new DOMParser().parseFromString(xml, 'application/xml')
  expect(parsed.querySelector('base-config').getAttribute('cleartextTrafficPermitted')).toBe('false')
  expect([...parsed.querySelectorAll('domain')].map(d => d.textContent)).toEqual(['8.148.198.200'])
  expect(parsed.querySelector('domain').getAttribute('includeSubdomains')).toBe('false')
  expect(renderAndroidNetworkSecurityConfig({ ...android, value: 'https://wms.example.com', allowHttp: false })).not.toContain('cleartextTrafficPermitted="true"')
  expect(() => renderAndroidNetworkSecurityConfig({ ...android, allowHttp: false })).toThrow('HTTPS')
})

it('validates the runtime base with the actual compiled platform', async () => {
  vi.stubEnv('WMS_BUILD_PLATFORM', 'app')
  vi.stubEnv('WMS_APP_PLATFORM', 'android')
  vi.stubEnv('VITE_API_BASE_URL', android.value)
  vi.stubEnv('VITE_ALLOW_HTTP_API', 'true')
  const { BASE_URL } = await import('../utils/api-base.js')
  expect(BASE_URL).toBe('http://8.148.198.200:8080')
  vi.resetModules()
  vi.stubEnv('VITE_ALLOW_HTTP_API', 'false')
  await expect(import('../utils/api-base.js')).rejects.toThrow('HTTPS')
})

it('validates an Android origin in a JS context with no browser URL API', () => {
  const source = fs.readFileSync('utils/api-base-policy.js', 'utf8').replace('export function', 'function')
  expect(vm.runInNewContext(`${source}\nvalidateOwnerApiBase(options)`, {options: android})).toBe('http://8.148.198.200:8080')
})

it('keeps release identity separate from the warehouse app', () => {
  const manifest = JSON.parse(fs.readFileSync('manifest.json', 'utf8'))
  expect(manifest.appid).toBe('__UNI__344493F')
  expect(manifest.versionName).toBe('1.0.0')
  expect(manifest.versionCode).toBe('100')
  const xml = fs.readFileSync('AndroidManifest.xml', 'utf8')
  expect(xml).toContain('package="com.jinqiaorongtong.wmsownersale"')
  expect(manifest['app-plus'].modules).toHaveProperty('Barcode')
  expect(manifest['app-plus'].distribute.android.permissions.some(entry => entry.includes('<uses-feature'))).toBe(false)
  const parsed = new DOMParser().parseFromString(xml, 'application/xml')
  expect(parsed.querySelector('parsererror')).toBeNull()
  const features = [...parsed.querySelectorAll('uses-feature')]
  expect(features.map(feature => feature.getAttribute('android:name'))).toEqual([
    'android.hardware.camera', 'android.hardware.camera.autofocus',
  ])
  for (const feature of features) {
    expect(feature.getAttribute('android:required')).toBe('false')
    expect(feature.getAttribute('tools:node')).toBe('replace')
  }
})

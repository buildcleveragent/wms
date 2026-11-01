import { afterEach, describe, expect, it, vi } from 'vitest'
import { validateBossApiBase } from '../utils/api-base-policy.mjs'
import { renderAndroidNetworkSecurityConfig } from '../config/androidNetworkSecurity.mjs'

const http = { value: 'http://8.148.198.200:8080/', platform: 'app', mode: 'production', allowHttp: true }
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.resetModules() })

describe('temporary boss HTTP configuration', () => {
  it.each(['app', 'mp-weixin', 'h5'])('requires explicit opt-in on %s', platform => {
    expect(validateBossApiBase({ ...http, platform })).toBe('http://8.148.198.200:8080')
    expect(() => validateBossApiBase({ ...http, platform, allowHttp: false })).toThrow(/HTTPS/)
  })

  it.each(['http://user:pass@example.com', 'ftp://example.com', 'http://example.com/api', 'http://example.com?x=1'])('rejects malformed API origins: %s', value => {
    expect(() => validateBossApiBase({ ...http, value })).toThrow()
  })

  it('keeps private network opt-in separate from HTTP opt-in', () => {
    expect(() => validateBossApiBase({ ...http, value: 'http://192.168.1.2' })).toThrow(/Private API hosts/)
    expect(validateBossApiBase({ ...http, value: 'http://192.168.1.2', allowPrivate: true })).toBe('http://192.168.1.2')
  })

  it('preserves same-origin H5 and requires a configured native API', () => {
    expect(validateBossApiBase({ ...http, value: '', platform: 'h5' })).toBe('')
    expect(() => validateBossApiBase({ ...http, value: '' })).toThrow(/required/)
  })

  it('validates the native API without a browser URL global', () => {
    vi.stubGlobal('URL', undefined)
    expect(validateBossApiBase(http)).toBe('http://8.148.198.200:8080')
  })

  it('uses the same opt-in during App runtime bootstrap', async () => {
    vi.stubEnv('DEV', false)
    vi.stubEnv('WMS_BUILD_PLATFORM', 'app')
    vi.stubEnv('VITE_API_BASE_URL', http.value)
    vi.stubEnv('VITE_ALLOW_HTTP_API', 'true')
    expect((await import('../utils/api-base.js')).BASE_URL).toBe('http://8.148.198.200:8080')
    vi.resetModules()
    vi.stubEnv('VITE_ALLOW_HTTP_API', 'false')
    await expect(import('../utils/api-base.js')).rejects.toThrow(/HTTPS/)
  })

  it('grants Android HTTP only to the configured host and removes it after HTTPS migration', () => {
    const xml = renderAndroidNetworkSecurityConfig(http)
    expect(xml).toContain('<base-config cleartextTrafficPermitted="false" />')
    expect(xml).toContain('<domain includeSubdomains="false">8.148.198.200</domain>')
    expect(xml.match(/<domain /g)).toHaveLength(1)
    const https = renderAndroidNetworkSecurityConfig({ ...http, value: 'https://api.example.com', allowHttp: false })
    expect(https).not.toContain('cleartextTrafficPermitted="true"')
  })
})

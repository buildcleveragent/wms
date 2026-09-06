import { describe, expect, it } from 'vitest'
import { renderAndroidNetworkSecurityConfig } from '../../config/androidNetworkSecurity.mjs'

describe('Android network security configuration', () => {
  it('allows cleartext for only the configured HTTP host and device loopback', () => {
    const xml = renderAndroidNetworkSecurityConfig('http://warehouse-api.lan:8001')
    expect(xml).toContain('<domain includeSubdomains="false">warehouse-api.lan</domain>')
    expect(xml).toContain('<domain includeSubdomains="false">127.0.0.1</domain>')
    expect(xml).toContain('<domain includeSubdomains="false">localhost</domain>')
    expect(xml.match(/<domain /g)).toHaveLength(3)
    expect(xml).toContain('<base-config cleartextTrafficPermitted="false" />')
  })

  it('creates no cleartext exception for HTTPS', () => {
    const xml = renderAndroidNetworkSecurityConfig('https://warehouse-api.example')
    expect(xml).toContain('<domain includeSubdomains="false">127.0.0.1</domain>')
    expect(xml).toContain('<domain includeSubdomains="false">localhost</domain>')
    expect(xml).not.toContain('warehouse-api.example</domain>')
    expect(xml).toContain('<base-config cleartextTrafficPermitted="false" />')
  })

  it('rejects non-HTTP API URLs', () => {
    expect(() => renderAndroidNetworkSecurityConfig('ftp://warehouse-api.lan')).toThrow(/http/)
  })
})

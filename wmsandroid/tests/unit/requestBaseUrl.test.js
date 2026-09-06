import { describe, expect, it } from 'vitest'
import { BASE_URL, normalizeApiBaseUrl } from '@/utils/request'

describe('Android API base URL', () => {
  it('uses the build-time LAN endpoint and removes trailing slashes', () => {
    expect(BASE_URL).toBe('http://127.0.0.1:8001')
    expect(normalizeApiBaseUrl(' http://wms-api.lan:8001/ ')).toBe('http://wms-api.lan:8001')
  })

  it('rejects non-http endpoint formats', () => {
    expect(() => normalizeApiBaseUrl('wms-api.lan:8001')).toThrow(/http/)
  })
})

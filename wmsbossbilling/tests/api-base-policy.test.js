import { describe, expect, it } from 'vitest'

import { validateApiBase } from '../../frontend/api-base-policy.mjs'

describe('shared API build policy', () => {
  it('uses same-origin only for H5 builds', () => {
    expect(validateApiBase({ value: '', platform: 'h5', mode: 'production' })).toBe('')
    expect(() =>
      validateApiBase({ value: '', platform: 'mp-weixin', mode: 'production' }),
    ).toThrow(/required/)
  })

  it('rejects unsafe URL components and transport', () => {
    expect(() =>
      validateApiBase({
        value: 'https://user:secret@example.com/api',
        platform: 'app',
        mode: 'production',
      }),
    ).toThrow(/credentials/)
    expect(() =>
      validateApiBase({
        value: 'http://api.example.com/api',
        platform: 'app',
        mode: 'production',
      }),
    ).toThrow(/HTTPS/)
  })

  it('rejects private production hosts unless explicitly authorized', () => {
    expect(() =>
      validateApiBase({
        value: 'https://192.168.10.2/api',
        platform: 'app',
        mode: 'production',
      }),
    ).toThrow(/Private API hosts/)
    expect(
      validateApiBase({
        value: 'https://192.168.10.2/api',
        platform: 'app',
        mode: 'production',
        allowPrivate: true,
      }),
    ).toBe('https://192.168.10.2/api')
  })

  it('accepts an HTTPS public API base', () => {
    expect(
      validateApiBase({
        value: 'https://wms.example.com/api/',
        platform: 'mp-weixin',
        mode: 'production',
      }),
    ).toBe('https://wms.example.com/api')
  })
})

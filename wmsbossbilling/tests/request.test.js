import { afterEach, describe, expect, it, vi } from 'vitest'

import { buildQuery, classifyError, request } from '../utils/request'

describe('boss request error classification', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows the server reason when a binary download is forbidden', async () => {
    vi.stubGlobal('uni', {
      getStorageSync: () => '',
      request: options => options.success({ statusCode: 403,
        data: new Uint8Array(Buffer.from(JSON.stringify({ detail: '没有导出权限' }))).buffer }),
    })
    await expect(request({ url: '/export/', responseType: 'arraybuffer' }))
      .rejects.toMatchObject({ kind: 'FORBIDDEN', message: '没有导出权限' })
  })

  it('preserves explicit ALL in navigation without changing API query defaults', () => {
    expect(buildQuery({ warehouse: '', owner: 1 })).toBe('owner=1')
    expect(buildQuery({ warehouse: '', owner: 1 }, { keepEmpty: ['warehouse'] }))
      .toBe('warehouse=&owner=1')
  })
  it.each([
    [0, 'NETWORK_ERROR'],
    [401, 'UNAUTHENTICATED'],
    [403, 'FORBIDDEN'],
    [500, 'SERVER_ERROR'],
    [422, 'BUSINESS_ERROR'],
  ])('classifies %s as %s', (status, kind) => {
    expect(classifyError(status, {}).kind).toBe(kind)
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let storage
let uniMock
let browser
let anchorClick

function response(status = 200, disposition = '') {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: vi.fn(() => disposition) },
    blob: vi.fn(async () => new Blob(['archive'])),
    json: vi.fn(async () => ({ detail: '导出失败' })),
  }
}

beforeEach(() => {
  vi.resetModules()
  vi.stubEnv('VITE_API_BASE_URL', 'http://127.0.0.1:8001')
  storage = new Map([
    ['access', 'account-a'],
    ['refresh', 'refresh-a'],
  ])
  uniMock = {
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    getStorageSync: vi.fn((key) => storage.get(key) || ''),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    request: vi.fn(),
    downloadFile: vi.fn(),
    showToast: vi.fn(),
    reLaunch: vi.fn(),
  }
  browser = {
    fetch: vi.fn(),
    URL: {
      createObjectURL: vi.fn(() => 'blob:all-products'),
      revokeObjectURL: vi.fn(),
    },
  }
  vi.stubGlobal('uni', uniMock)
  vi.stubGlobal('window', browser)
  anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  document.body.innerHTML = ''
})

function refreshSuccessfully(options) {
  expect(options.url).toBe('http://127.0.0.1:8001/api/pda/token/refresh/')
  expect(options.data).toEqual({ refresh: 'refresh-a' })
  options.success({ statusCode: 200, data: { access: 'fresh-access', refresh: 'refresh-b' } })
}

describe('all-product archive H5 download', () => {
  it.each([
    ['', '全部商品档案.xlsx'],
    [`attachment; filename*=UTF-8''${encodeURIComponent('全部商品-2026.xlsx')}`, '全部商品-2026.xlsx'],
  ])('downloads all owners with auth and the expected filename', async (header, filename) => {
    browser.fetch.mockResolvedValue(response(200, header))
    const { downloadAllProductArchives } = await import('@/utils/request')

    await expect(downloadAllProductArchives()).resolves.toEqual({
      opened: true,
      tempFilePath: '',
      filename,
    })

    expect(browser.fetch).toHaveBeenCalledExactlyOnceWith(
      'http://127.0.0.1:8001/api/products/export-all-excel/',
      { headers: { Authorization: 'Bearer account-a' } },
    )
    expect(anchorClick).toHaveBeenCalledTimes(1)
    expect(anchorClick.mock.instances[0].download).toBe(filename)
    expect(anchorClick.mock.instances[0].href).toBe('blob:all-products')
    expect(document.querySelector('a')).toBeNull()
    expect(browser.URL.revokeObjectURL).toHaveBeenCalledWith('blob:all-products')
    expect(uniMock.downloadFile).not.toHaveBeenCalled()
  })

  it('refreshes an expired access token and retries the complete export', async () => {
    browser.fetch.mockResolvedValueOnce(response(401)).mockResolvedValueOnce(response())
    uniMock.request.mockImplementation(refreshSuccessfully)
    const { downloadAllProductArchives } = await import('@/utils/request')

    await expect(downloadAllProductArchives()).resolves.toMatchObject({ opened: true })

    expect(uniMock.request).toHaveBeenCalledTimes(1)
    expect(browser.fetch).toHaveBeenCalledTimes(2)
    expect(browser.fetch.mock.calls[1]).toEqual([
      'http://127.0.0.1:8001/api/products/export-all-excel/',
      { headers: { Authorization: 'Bearer fresh-access' } },
    ])
    expect(storage.get('refresh')).toBe('refresh-b')
    expect(anchorClick).toHaveBeenCalledTimes(1)
  })

  it('rejects permission denial without saving a response or refreshing tokens', async () => {
    browser.fetch.mockResolvedValue(response(403))
    const { downloadAllProductArchives } = await import('@/utils/request')

    await expect(downloadAllProductArchives()).rejects.toMatchObject({
      statusCode: 403,
      message: '无商品档案导出权限',
    })

    expect(anchorClick).not.toHaveBeenCalled()
    expect(browser.URL.createObjectURL).not.toHaveBeenCalled()
    expect(uniMock.request).not.toHaveBeenCalled()
    expect(storage.get('access')).toBe('account-a')
  })

  it('discards an archive whose body finishes after the user switches accounts', async () => {
    let finishBlob
    const downloadResponse = response()
    downloadResponse.blob.mockImplementation(() => new Promise((resolve) => {
      finishBlob = resolve
    }))
    browser.fetch.mockResolvedValue(downloadResponse)
    const { downloadAllProductArchives, setAuthTokens } = await import('@/utils/request')
    const pending = downloadAllProductArchives()
    const rejected = expect(pending).rejects.toMatchObject({
      statusCode: 409,
      sessionChanged: true,
    })

    await vi.waitFor(() => expect(finishBlob).toBeTypeOf('function'))
    setAuthTokens({ access: 'account-b', refresh: 'refresh-b' })
    finishBlob(new Blob(['previous account archive']))
    await rejected

    expect(anchorClick).not.toHaveBeenCalled()
    expect(browser.URL.createObjectURL).not.toHaveBeenCalled()
    expect(storage.get('access')).toBe('account-b')
  })

  it('preserves selected-owner export and its filename after sharing the downloader', async () => {
    browser.fetch.mockResolvedValue(response())
    const { downloadProductArchive } = await import('@/utils/request')

    await expect(downloadProductArchive(7, 'OWNER7')).resolves.toMatchObject({
      opened: true,
      filename: '商品档案-OWNER7.xlsx',
    })
    expect(browser.fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:8001/api/products/export-excel/?owner_id=7',
      { headers: { Authorization: 'Bearer account-a' } },
    )
  })
})

describe('all-product archive native download', () => {
  beforeEach(() => vi.stubGlobal('window', undefined))

  it('returns the downloaded Excel path for saving and opening on the PDA', async () => {
    uniMock.downloadFile.mockImplementation((options) => {
      options.success({ statusCode: 200, tempFilePath: '/tmp/all-products.xlsx' })
    })
    const { downloadAllProductArchives } = await import('@/utils/request')

    await expect(downloadAllProductArchives()).resolves.toEqual({
      opened: false,
      tempFilePath: '/tmp/all-products.xlsx',
    })
    expect(uniMock.downloadFile).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      url: 'http://127.0.0.1:8001/api/products/export-all-excel/',
      header: { Authorization: 'Bearer account-a' },
    }))
  })

  it('retries native downloads with a refreshed token', async () => {
    uniMock.request.mockImplementation(refreshSuccessfully)
    uniMock.downloadFile.mockImplementation((options) => {
      options.success(options.header.Authorization === 'Bearer account-a'
        ? { statusCode: 401 }
        : { statusCode: 200, tempFilePath: '/tmp/all-products.xlsx' })
    })
    const { downloadAllProductArchives } = await import('@/utils/request')

    await expect(downloadAllProductArchives()).resolves.toMatchObject({
      tempFilePath: '/tmp/all-products.xlsx',
    })
    expect(uniMock.downloadFile).toHaveBeenCalledTimes(2)
    expect(uniMock.downloadFile.mock.calls[1][0].header.Authorization).toBe('Bearer fresh-access')
    expect(uniMock.request).toHaveBeenCalledTimes(1)
  })

  it('rejects native permission denial without exposing a downloaded error file', async () => {
    uniMock.downloadFile.mockImplementation((options) => {
      options.success({ statusCode: 403, tempFilePath: '/tmp/error-body.json' })
    })
    const { downloadAllProductArchives } = await import('@/utils/request')

    await expect(downloadAllProductArchives()).rejects.toMatchObject({
      statusCode: 403,
      message: '无商品档案导出权限',
    })
    expect(uniMock.request).not.toHaveBeenCalled()
  })

  it.each([200, 401])('discards late native %s after account switching', async (statusCode) => {
    let finishDownload
    uniMock.downloadFile.mockImplementation((options) => { finishDownload = options.success })
    const { downloadAllProductArchives, setAuthTokens } = await import('@/utils/request')
    const pending = downloadAllProductArchives()
    const rejected = expect(pending).rejects.toMatchObject({
      statusCode: 409,
      sessionChanged: true,
    })

    setAuthTokens({ access: 'account-b', refresh: 'refresh-b' })
    finishDownload({ statusCode, tempFilePath: '/tmp/previous-account.xlsx' })
    await rejected

    expect(uniMock.downloadFile).toHaveBeenCalledTimes(1)
    expect(uniMock.request).not.toHaveBeenCalled()
    expect(storage.get('access')).toBe('account-b')
  })
})

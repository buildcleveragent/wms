import fs from 'node:fs'
import vm from 'node:vm'
import { expect, it, vi } from 'vitest'

// Execute the same source with H5 branches removed, as the mini-program compiler does.
function nativeClient({ status = 200, payload = 'PKworkbook', delayed = false, android = false, readFails = false } = {}) {
  const source = fs.readFileSync(`${process.cwd()}/utils/request.js`, 'utf8')
    .replace(/^import .*$/gm, '')
    .replace(/^export \{.*$/gm, '')
    .replace(/\/\/ #ifdef H5[\s\S]*?\/\/ #endif/g, '')
    .replace(/^export /gm, '')
  let finish
  const files = {
    readFile: vi.fn(options => options.success({data: options.encoding ? payload : new TextEncoder().encode(payload).buffer})),
    unlink: vi.fn(),
  }
  const uni = {
    getStorageSync: () => 'test-token', setStorageSync: vi.fn(),
    getFileSystemManager: () => files,
    downloadFile: vi.fn(options => {
      finish = () => options.success({statusCode: status, tempFilePath: '/private/export.xlsx'})
      if (!delayed) finish()
    }),
    openDocument: vi.fn(options => options.success()),
  }
  const plus = {io: {
    resolveLocalFileSystemURL: vi.fn((path, success) => success({
      file: success => success({}), remove: success => { files.unlink(); success() },
    })),
    FileReader: class {
      readAsText() { if (readFails) this.onerror({}); else this.onload({target: {result: payload}}) }
      readAsDataURL() { if (readFails) this.onerror({}); else this.onload({target: {result: `data:application/octet-stream;base64,${Buffer.from(payload).toString('base64')}`}}) }
    },
  }}
  if (android) {
    delete uni.getFileSystemManager
    uni.base64ToArrayBuffer = data => Uint8Array.from(Buffer.from(data, 'base64')).buffer
  }
  const context = vm.createContext({uni, ...(android ? {plus} : {}), BASE_URL: 'http://8.148.198.200:8080', Uint8Array, TextDecoder, useCart: () => ({})})
  vm.runInContext(`${source}\nthis.client = {downloadAuthenticatedFile, setTokens};`, context)
  return {client: context.client, uni, files, finish: () => finish()}
}

it('opens a verified Excel file with the save/share menu', async () => {
  const {client, uni} = nativeClient()
  await client.downloadAuthenticatedFile('/export/', '库存.xlsx', {excel: true})
  expect(uni.downloadFile.mock.calls[0][0].header.Authorization).toBe('Bearer test-token')
  expect(uni.openDocument).toHaveBeenCalledWith(expect.objectContaining({showMenu: true, filePath: '/private/export.xlsx'}))
})

it('Android verifies Excel through HTML5+ IO at the public API origin', async () => {
  const {client, uni} = nativeClient({android: true})
  await client.downloadAuthenticatedFile('/api/export/', '库存.xlsx', {excel: true})
  expect(uni.downloadFile.mock.calls[0][0].url).toBe('http://8.148.198.200:8080/api/export/')
  expect(uni.openDocument).toHaveBeenCalledTimes(1)
})

it.each([200, 413])('Android blocks error JSON with HTTP %s', async status => {
  const {client, uni, files} = nativeClient({android: true, status, payload: '{"detail":"请缩小筛选范围"}'})
  await expect(client.downloadAuthenticatedFile('/api/export/', '库存.xlsx', {excel: true})).rejects.toMatchObject({message: '请缩小筛选范围'})
  expect(uni.openDocument).not.toHaveBeenCalled()
  expect(files.unlink).toHaveBeenCalled()
})

it('Android refuses to open a file when IO verification fails', async () => {
  const {client, uni} = nativeClient({android: true, readFails: true})
  await expect(client.downloadAuthenticatedFile('/api/export/', '库存.xlsx', {excel: true})).rejects.toMatchObject({message: '下载文件读取失败，请重试'})
  expect(uni.openDocument).not.toHaveBeenCalled()
})

it.each([200, 413])('does not open error JSON with HTTP %s', async status => {
  const {client, uni} = nativeClient({status, payload: '{"detail":"请缩小筛选范围"}'})
  await expect(client.downloadAuthenticatedFile('/export/', '库存.xlsx', {excel: true}))
    .rejects.toMatchObject({message: '请缩小筛选范围'})
  expect(uni.openDocument).not.toHaveBeenCalled()
})

it.each(['page', 'account'])('discards completed downloads after %s changes', async change => {
  const {client, uni, files, finish} = nativeClient({delayed: true})
  const pending = client.downloadAuthenticatedFile('/export/', '库存.xlsx', {excel: true, isCurrent: () => change !== 'page'}).catch(error => error)
  if (change === 'account') client.setTokens('new-account', 'new-refresh')
  finish()
  const result = await pending
  expect(change === 'page' ? result : result.statusCode).toBe(change === 'page' ? false : 409)
  expect(uni.openDocument).not.toHaveBeenCalled()
  expect(files.unlink).toHaveBeenCalled()
})

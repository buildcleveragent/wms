import fs from 'node:fs'
import vm from 'node:vm'
import { expect, it, vi } from 'vitest'

// Execute the same source with H5 branches removed, as the mini-program compiler does.
function nativeClient({ status = 200, payload = 'PKworkbook', delayed = false } = {}) {
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
  const context = vm.createContext({uni, BASE_URL: '', Uint8Array, TextDecoder, useCart: () => ({})})
  vm.runInContext(`${source}\nthis.client = {downloadAuthenticatedFile, setTokens};`, context)
  return {client: context.client, uni, files, finish: () => finish()}
}

it('opens a verified Excel file with the save/share menu', async () => {
  const {client, uni} = nativeClient()
  await client.downloadAuthenticatedFile('/export/', '库存.xlsx', {excel: true})
  expect(uni.downloadFile.mock.calls[0][0].header.Authorization).toBe('Bearer test-token')
  expect(uni.openDocument).toHaveBeenCalledWith(expect.objectContaining({showMenu: true, filePath: '/private/export.xlsx'}))
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

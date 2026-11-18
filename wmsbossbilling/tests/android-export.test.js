// @vitest-environment node
import fs from 'node:fs/promises'
import { createRequire } from 'node:module'
import { build } from 'esbuild'
import { beforeAll, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const { preprocess } = require('@dcloudio/uni-cli-shared/lib/preprocess')
let compiled

beforeAll(async () => {
  // Execute the actual App branch; Vitest alone leaves uni-app directives as comments.
  const result = await build({
    entryPoints: ['utils/request.js'], bundle: true, write: false, format: 'cjs',
    define: { 'import.meta.env': JSON.stringify({
      WMS_BUILD_PLATFORM: 'app', VITE_API_BASE_URL: 'https://example.test', MODE: 'production',
    }) },
    plugins: [{ name: 'app-conditions', setup(builder) {
      builder.onLoad({ filter: /\.m?js$/ }, async ({ path }) => ({
        contents: preprocess(await fs.readFile(path, 'utf8'), { APP_PLUS: true, APP: true, H5: false }, { type: 'js' }),
        loader: 'js',
      }))
    } }],
  })
  compiled = result.outputFiles[0].text
})

function runtime({ failWrite = false, status = 200, fileSize } = {}) {
  const data = new Uint8Array([80, 75, 3, 4, 0, 128, 255]).buffer
  const output = {}, bytes = { nativeByteArray: true }
  const plus = {
    os: { name: 'Android' },
    io: {
      convertLocalFileSystemURL: vi.fn(path => `/private/${path.slice(5)}`),
      getFileInfo: ({ success }) => success({ size: fileSize ?? data.byteLength }),
    },
    android: {
      newObject: vi.fn(() => output),
      invoke: vi.fn((object, method) => {
        if (method === 'decode') return bytes
        if (method === 'write' && failWrite) throw new Error('disk full')
      }),
    },
    runtime: { openFile: vi.fn() },
  }
  const uni = {
    getStorageSync: () => '',
    request: options => options.success({ statusCode: status, data,
      header: { 'content-disposition': 'attachment; filename="report.xlsx"' } }),
    arrayBufferToBase64: value => Buffer.from(value).toString('base64'),
    showToast: vi.fn(),
  }
  const module = { exports: {} }
  new Function('module', 'exports', 'uni', 'plus', compiled)(module, module.exports, uni, plus)
  return { download: module.exports.downloadBinary, plus, uni, output, bytes, data }
}

it('exports binary workbooks using Android APIs without mini-program filesystem APIs', async () => {
  const { download, plus, output, bytes, data } = runtime()
  await expect(download({ url: '/api/reports/boss/exports/', method: 'POST' }))
    .resolves.toMatchObject({ filename: 'report.xlsx' })
  expect(plus.android.invoke).toHaveBeenCalledWith('android.util.Base64', 'decode', Buffer.from(data).toString('base64'), 0)
  expect(plus.android.invoke).toHaveBeenCalledWith(output, 'write', bytes)
  expect(plus.android.invoke).toHaveBeenCalledWith(output, 'close')
  expect(plus.runtime.openFile).toHaveBeenCalledWith('_doc/report.xlsx', {}, expect.any(Function))
})

it('closes the file and reports a failed write without opening a partial workbook', async () => {
  const { download, plus, output } = runtime({ failWrite: true })
  await expect(download({ url: '/api/reports/boss/exports/' })).rejects.toThrow('disk full')
  expect(plus.android.invoke).toHaveBeenCalledWith(output, 'close')
  expect(plus.runtime.openFile).not.toHaveBeenCalled()
})

it('explains that a saved workbook needs an installed viewer when opening fails', async () => {
  const { download, plus, uni } = runtime()
  await download({ url: '/api/reports/boss/exports/' })
  plus.runtime.openFile.mock.calls[0][2]({ message: 'no viewer' })
  expect(uni.showToast).toHaveBeenCalledWith(expect.objectContaining({ icon: 'none', title: expect.stringContaining('文件已保存') }))
})

it('does not write or open a file when the export is forbidden', async () => {
  const { download, plus } = runtime({ status: 403 })
  await expect(download({ url: '/api/reports/boss/exports/' })).rejects.toMatchObject({ statusCode: 403 })
  expect(plus.android.newObject).not.toHaveBeenCalled()
  expect(plus.runtime.openFile).not.toHaveBeenCalled()
})

it('rejects a silent native write failure instead of opening an incomplete file', async () => {
  const { download, plus } = runtime({ fileSize: 0 })
  await expect(download({ url: '/api/reports/boss/exports/' })).rejects.toThrow('写入不完整')
  expect(plus.runtime.openFile).not.toHaveBeenCalled()
})

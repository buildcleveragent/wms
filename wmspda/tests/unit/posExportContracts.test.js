import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const root = path.resolve(import.meta.dirname, '../..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

describe('POS Excel export contracts', () => {
  it('opts every POS home export into the all-information worksheet', () => {
    const page = read('pages/pos/index.vue')
    const request = read('utils/request.js')
    const report = read('pages/pos/report.vue')

    expect(page.match(/include_all_sheet: 1/g)).toHaveLength(4)
    expect(request).toContain('include_all_sheet: params.include_all_sheet')
    expect(request).toContain('posShiftExportUrl: (id, params = {}) =>')
    expect(report).not.toContain('include_all_sheet')
  })

  it('uses meaningful dated names for statistics, sales, and shift exports', () => {
    const page = read('pages/pos/index.vue')

    expect(page).toContain("posExportFilename('POS今日统计')")
    expect(page).toContain("posExportFilename('POS销售历史')")
    expect(page).toContain('posExportFilename(`POS班次_${shiftNo}`)')
    expect(page).toContain(".replace(/[\\\\/:*?\"<>|\\u0000-\\u001f]/g, '_')")
  })

  it('sets the real filename for authenticated H5 and APP-PLUS downloads', () => {
    const page = read('pages/pos/index.vue')

    expect(page).toContain('plus.downloader.createDownload(')
    expect(page).toContain('{ filename: `_downloads/${filename}` }')
    expect(page).toContain('task.setRequestHeader(name, value)')
    expect(page).toContain('window.fetch(url, { headers: authHeader() })')
    expect(page).toContain('anchor.download = filename')
  })
})

import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const root = path.resolve(import.meta.dirname, '../..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

describe('product archive export contracts', () => {
  it('keeps the workbench entry behind the export capability', () => {
    const home = read('pages/index/index.vue')
    const auth = read('store/auth.js')

    expect(home).toContain('requiresProductExport: true')
    expect(home).toContain('auth.canExportProducts')
    expect(auth).toContain('can_export_products === true')
  })

  it('requires one selected owner and prevents duplicate downloads', () => {
    const page = read('pages/products/export.vue')

    expect(page).toContain(':disabled="!selectedOwner || busy || !canExport"')
    expect(page).toContain('if (!selectedOwner.value || busy.value) return')
    expect(page).toContain('Number(result?.count) === 1')
  })

  it('supports authenticated H5 and app downloads', () => {
    const request = read('utils/request.js')

    expect(request).toContain('/api/products/export-excel/?owner_id=')
    expect(request).toContain('window.fetch(url')
    expect(request).toContain('uni.downloadFile({')
  })

  it('exports missing GTIN products directly from the permission-gated home card', () => {
    const home = read('pages/index/index.vue')
    const request = read('utils/request.js')

    expect(home).toContain('key: "missing-gtin-export"')
    expect(home).toContain('requiresProductExport: true')
    expect(home).toContain('allProductExporting.value || missingGtinExporting.value')
    expect(home).toContain("exportProducts(missingGtinExporting, downloadMissingGtinProducts, '无标准条码商品')")
    expect(home).toContain("title: '正在导出…'")
    expect(request).toContain('export function downloadMissingGtinProducts')
    expect(request).toContain('/api/products/export-missing-gtin/')
    expect(request).toContain('() => downloadMissingGtinProducts(true)')
  })
})

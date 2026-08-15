import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const root = path.resolve(import.meta.dirname, '../..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

describe('product import contracts', () => {
  it('exposes receipt capability and warehouse options', () => {
    const auth = read('store/auth.js')
    const request = read('utils/request.js')

    expect(auth).toContain('can_receive_without_order === true')
    expect(request).toContain('/api/products/import-warehouses/')
    expect(request).toContain("formData: warehouseId ? { warehouse_id: String(warehouseId) } : {}")
  })

  it('allows product imports to run for up to ten minutes', () => {
    const manifest = read('manifest.json')
    const request = read('utils/request.js')

    expect(manifest).toContain('"uploadFile" : 600000')
    expect(request).toContain('const PRODUCT_IMPORT_UPLOAD_TIMEOUT_MS = 10 * 60 * 1000')
    expect(request).toContain('timeout: PRODUCT_IMPORT_UPLOAD_TIMEOUT_MS')
  })

  it('keeps warehouse optional for archive-only imports', () => {
    const page = read('pages/products/import.vue')

    expect(page).toContain('不选择（仅建档）')
    expect(page).toContain('selectedWarehouse.value?.id || null')
    expect(page).toContain('result.receipts?.length')
  })

  it('scrolls the completed import report into view', () => {
    const page = read('pages/products/import.vue')

    expect(page).toContain('id="product-import-result"')
    expect(page).toContain("selector: '#product-import-result'")
    expect(page).toContain('await scrollToResult()')
  })
})

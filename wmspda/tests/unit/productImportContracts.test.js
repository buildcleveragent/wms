import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const root = path.resolve(import.meta.dirname, '../..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

describe('product import v6 contracts', () => {
  it('exposes receipt capability and warehouse options', () => {
    const auth = read('store/auth.js')
    const request = read('utils/request.js')

    expect(auth).toContain('can_receive_without_order === true')
    expect(request).toContain('/api/products/import-warehouses/')
    expect(request).toContain("formData: warehouseId ? { warehouse_id: String(warehouseId) } : {}")
  })

  it('keeps warehouse optional for archive-only imports', () => {
    const page = read('pages/products/import.vue')

    expect(page).toContain('不选择（仅建档）')
    expect(page).toContain('selectedWarehouse.value?.id || null')
    expect(page).toContain('result.receipts?.length')
  })
})

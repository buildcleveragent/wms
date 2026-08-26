import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const root = path.resolve(import.meta.dirname, '../..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

describe('company inventory pagination contracts', () => {
  it('defaults the desktop-oriented inventory page to 100 rows', () => {
    const page = read('pages/inventory/company.vue')

    expect(page).toContain('const pageSize = ref(100)')
    expect(page).toContain('const size = Number(pageSize.value || 100)')
  })

  it('offers 500 and 1000 row choices and falls back to 100', () => {
    const page = read('pages/inventory/company.vue')

    expect(page).toContain('const pageSizeOptions = [10, 20, 50, 100, 500, 1000]')
    expect(page).toContain('pageSize.value = pageSizeOptions[idx] || 100')
    expect(page).toContain('page.value = 1')
  })

  it('uses 100 rows for company inventory requests by default', () => {
    const request = read('utils/request.js')

    expect(request).toMatch(
      /companyInventorySummary:[\s\S]*?page_size: params\.page_size \|\| 100/,
    )
  })
})

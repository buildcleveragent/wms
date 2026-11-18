import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CompanyInventory from '@/pages/inventory/company.vue'

const api = vi.hoisted(() => ({
  companyInventorySummary: vi.fn(), companyInventoryWarehouses: vi.fn(), owners: vi.fn(),
}))
vi.mock('@/utils/request', () => ({ api }))

describe('company inventory quantity display', () => {
  beforeEach(() => {
    for (const fn of Object.values(api)) fn.mockReset()
    api.companyInventoryWarehouses.mockResolvedValue([])
    api.owners.mockResolvedValue([])
  })

  it.each([
    ['12345678901234.5678', '12345678901234.5678'],
    ['99999999999999.9999', '99999999999999.9999'],
    ['0.0001', '0.0001'], ['12.5000', '12.5'], ['0.0000', '0'],
    [0, '0'], [null, '-'], [undefined, '-'], ['', '-'],
  ])('renders every quantity column exactly: %s', async (value, expected) => {
    const row = { product_id: 1, product_name: '测试库存', base_unit_name: '瓶' }
    for (const key of ['onhand', 'available', 'allocated', 'locked', 'damaged']) row[`${key}_qty`] = value
    api.companyInventorySummary.mockResolvedValue({ count: 1, results: [row] })
    const wrapper = mount(CompanyInventory, {
      global: { stubs: { InventoryNameSelect: true } },
    })
    await flushPromises()
    const numbers = wrapper.findAll('.table-row .number')
    expect(numbers).toHaveLength(5)
    expect(numbers.map(cell => cell.text())).toEqual(Array(5).fill(expected))
    wrapper.unmount()
  })
})

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  onLoad: null,
  onUnload: null,
  preview: vi.fn(),
  bills: vi.fn(),
  accruals: vi.fn(),
  fetchPeriods: vi.fn(),
}))

vi.mock('@dcloudio/uni-app', () => ({
  onLoad: (callback) => { mocks.onLoad = callback },
  onPullDownRefresh: vi.fn(),
  onUnload: (callback) => { mocks.onUnload = callback },
}))

vi.mock('@/utils/request', () => ({
  api: {
    billingPeriodPreview: mocks.preview,
    billingBills: mocks.bills,
    billingAccruals: mocks.accruals,
    billingPeriods: vi.fn(),
  },
  fetchAllPages: mocks.fetchPeriods,
}))

import BillingOverview from '@/pages/billing/overview.vue'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function preview(total) {
  return {
    accrual_count: 1,
    subtotal: String(total - 2),
    tax_total: '2',
    total: String(total),
    by_charge_type: [{ charge_type: 'PICK', accrual_count: 1, subtotal: '10', tax_total: '1', total: '15' }],
    by_status: [{ status: 'OPEN', accrual_count: 1, subtotal: '10', tax_total: '1', total: String(total) }],
    by_service_date: [{ service_date: '2026-08-04', accrual_count: 1, subtotal: '10', tax_total: '1', total: String(total) }],
  }
}

function render() {
  return mount(BillingOverview, {
    global: {
      config: {
        compilerOptions: {
          isCustomElement: (tag) => ['view', 'text', 'picker'].includes(tag),
        },
      },
    },
  })
}

describe('计费总览异步一致性', () => {
  beforeEach(() => {
    mocks.preview.mockReset()
    mocks.bills.mockReset()
    mocks.accruals.mockReset()
    mocks.fetchPeriods.mockReset()
    vi.stubGlobal('uni', {
      stopPullDownRefresh: vi.fn(),
      navigateTo: vi.fn(),
      showToast: vi.fn(),
    })
  })

  it('切换账期后丢弃旧账期的迟到失败，并直接使用服务端分组 total', async () => {
    const oldPreview = deferred()
    mocks.fetchPeriods.mockResolvedValue([
      { id: 1, label: '账期 A', owner: 1, owner_name: '货主', warehouse: 2, warehouse_name: '仓库', status: 'OPEN', start_date: '2026-07-01', end_date: '2026-07-31' },
      { id: 2, label: '账期 B', owner: 1, owner_name: '货主', warehouse: 2, warehouse_name: '仓库', status: 'OPEN', start_date: '2026-08-01', end_date: '2026-08-31' },
    ])
    mocks.preview.mockImplementation((id) => id === 1 ? oldPreview.promise : Promise.resolve(preview(22)))
    mocks.bills.mockResolvedValue({ results: [] })
    mocks.accruals.mockResolvedValue({ results: [] })

    const wrapper = render()
    mocks.onLoad({ period: '1' })
    await flushPromises()

    await wrapper.findAll('picker')[1].trigger('change', { detail: { value: 1 } })
    await flushPromises()
    expect(wrapper.text()).toContain('账期 B')
    expect(wrapper.text()).toContain('¥22.00')
    expect(wrapper.text()).toContain('¥15.00')

    oldPreview.reject(new Error('A 请求失败'))
    await flushPromises()
    expect(wrapper.text()).toContain('账期 B')
    expect(wrapper.text()).toContain('¥22.00')
    expect(wrapper.text()).not.toContain('当前账期加载失败')
  })

  it('账期列表失败与空账期使用不同状态并可重试', async () => {
    mocks.fetchPeriods.mockRejectedValueOnce(new Error('网络失败'))
    const wrapper = render()
    await mocks.onLoad({})
    await flushPromises()

    expect(wrapper.text()).toContain('网络失败')
    expect(wrapper.text()).toContain('重试加载账期')
    expect(wrapper.text()).not.toContain('还没有可展示的账期')
  })
})

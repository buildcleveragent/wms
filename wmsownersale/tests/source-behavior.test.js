import fs from 'node:fs'
import { describe, expect, it } from 'vitest'

const root = process.cwd()
const read = (relative) => fs.readFileSync(`${root}/${relative}`, 'utf8')

describe('页面异步状态与移动端结构契约', () => {
  it('商品分页只由内部 scrolltolower 触发且旧搜索响应被丢弃', () => {
    const source = read('pages/products/search.vue')
    expect(source).toContain('@scrolltolower="loadProducts"')
    expect(source).not.toContain('onReachBottom')
    expect(source).toContain('generation !== searchGeneration')
    expect(source).toContain('!list.value.next')
  })

  it('库存表使用页面纵向翻页、横向滚动和刷新代次', () => {
    const source = read('pages/inventory/index.vue')
    expect(source).toContain('scroll-x')
    expect(source).not.toContain('scroll-y')
    expect(source).toContain('onReachBottom(loadMore)')
    expect(source).toContain('onPullDownRefresh(refreshPage)')
    expect(source).not.toContain('@scrolltolower')
    expect(source).toMatch(/\.table-content\s*\{[^}]*min-width:/)
    expect(source).not.toMatch(/\.table-scroll\s*\{[^}]*height:/)
    expect(source).toContain('page_size: PAGE_SIZE')
    expect(source).toContain('const PAGE_SIZE = 50')
    expect(source).toContain('generation !== requestGeneration')
    expect(source).toContain('uni.stopPullDownRefresh()')

    const requestSource = read('utils/request.js')
    expect(requestSource).toMatch(/inventorySummary:[\s\S]*page_size: params\.page_size \|\| 50/)

    const pages = JSON.parse(read('pages.json').replace(/\/\/.*$/gm, ''))
    const inventory = pages.pages.find((page) => page.path === 'pages/inventory/index')
    expect(inventory?.style?.enablePullDownRefresh).toBe(true)
  })

  it('报表只允许最新请求写入且错误时清除旧条件数据', () => {
    const source = read('pages/reports/operations.vue')
    expect(source).toContain('generation !== requestGeneration')
    expect(source).toContain('clearResults()')
    expect(source).toContain('履约报表加载失败')
  })

  it('客户页具有明确状态且不存在无事件的下一步按钮', () => {
    const source = read('pages/customers/select.vue')
    expect(source).toContain('firstLoading')
    expect(source).toContain('loadingMore')
    expect(source).toContain('客户加载失败')
    expect(source).toContain('没有符合条件的客户')
    expect(source).not.toContain('下一步：选品')
  })
})

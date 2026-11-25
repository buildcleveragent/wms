import { mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

const require = createRequire(new URL('../wmspda/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const [origin, summaryPath, artifacts] = process.argv.slice(2)
const libraryPath = '/static/vendor/echarts/6.1.0/echarts.common.min.js'
const dates = ['2026-01-01', '2026-01-02']

function summary(empty) {
  const trend = empty ? [0, 0] : [3, 5]
  const orders = { dates, total: trend, finished: trend }
  const ranking = empty ? { labels: [], values: [] } : { labels: ['甲'], values: [3] }
  const tasks = empty ? { total: 0, done: 0 } : { total: 8, done: 6 }
  return {
    ok: true,
    data: {
      data_as_of: '2026-01-02T09:00:00+08:00', range: { start: dates[0], end: dates[1] },
      kpi: { putaway: tasks, pick: tasks },
      no_order_receive: {
        available: true, trend: { dates, qty: trend },
        summary: { qty: empty ? '0.000' : '8.000', orders: empty ? 0 : 2, lines: empty ? 0 : 2 },
      },
      pos: {
        available: true, trend_30d: { dates, net_amount: trend },
        today: {
          cashiers: empty ? [] : [{ cashier_username: '乙', net_amount: '5.00' }],
          summary: { net_amount: trend[1], received_amount: trend[1], completed_count: empty ? 0 : 1 },
        },
      },
      inbound_ts: orders, outbound_ts: orders, inbound_backlog: ranking, outbound_backlog: ranking,
      eff_putaway: ranking, eff_pick: ranking, eff_pack: ranking,
    },
  }
}

await mkdir(artifacts, { recursive: true })
const browser = await chromium.launch()
try {
  for (const [scenario, width] of [
    ['data', 1280], ['data', 390], ['empty', 1280], ['api-error', 1280], ['library-error', 1280],
  ]) {
    const page = await browser.newPage({ viewport: { width, height: 844 }, serviceWorkers: 'block' })
    const external = []
    const errors = []
    let summaryRequests = 0
    let libraryRequests = 0
    let releaseLibrary
    const libraryMayLoad = new Promise(resolve => { releaseLibrary = resolve })
    await page.route('**/*', async route => {
      const url = new URL(route.request().url())
      if (url.origin !== origin) {
        external.push(url.origin)
        return route.abort('internetdisconnected')
      }
      if (url.pathname === summaryPath) {
        summaryRequests++
        if (scenario === 'api-error') return route.fulfill({ status: 503 })
        return route.fulfill({ json: summary(scenario === 'empty') })
      }
      if (url.pathname === libraryPath) {
        libraryRequests++
        if (scenario === 'library-error') return route.fulfill({ status: 404 })
        if (scenario === 'data') await libraryMayLoad
      }
      return route.continue()
    })
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => {
      if (message.type() !== 'error') return
      const url = message.location().url
      const expected = (scenario === 'api-error' && url === origin + summaryPath)
        || (scenario === 'library-error' && url === origin + libraryPath)
      if (!(expected && message.text().startsWith('Failed to load resource:'))) errors.push(message.text())
    })
    try {
      await page.goto(origin, { waitUntil: 'commit' })
      await expect(page).toHaveTitle('仓库运营看板')
      const kpi = page.locator('#k_put_rate')
      const charts = page.locator('.chart')
      await expect(charts).toHaveCount(10)
      if (scenario === 'data') {
        await expect(kpi).toHaveText('完成率 75%')
        expect(await page.evaluate(() => typeof window.echarts)).toBe('undefined')
        releaseLibrary()
        await expect(charts.locator('canvas')).toHaveCount(10)
        const rendered = await charts.evaluateAll(elements => elements.map(element => {
          const chart = window.echarts.getInstanceByDom(element)
          return chart.getWidth() > 0 && chart.getHeight() > 0
            && chart.getOption().series.some(series => series.data.some(value => Number(value) > 0))
        }))
        expect(rendered).toEqual(Array(10).fill(true))
        const trend = page.locator('#chart_in_ts')
        const position = await trend.evaluate(element => {
          const [x, y] = window.echarts.getInstanceByDom(element)
            .convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [1, 5])
          return { x, y }
        })
        await trend.hover({ position })
        await expect(trend.getByText('总单数', { exact: true })).toBeVisible()
        await page.mouse.move(0, 0)
        await expect(page.locator('#dashboard-warning')).toBeHidden()
        await expect(page.locator('#dashboard-error')).toBeHidden()
      } else if (scenario === 'empty') {
        await expect(kpi).toHaveText('完成率 0%')
        await expect(charts.locator('.chart-empty')).toHaveCount(10)
        await expect(charts.locator('canvas, .chart-failed')).toHaveCount(0)
        await expect(page.locator('#dashboard-error')).toBeHidden()
      } else if (scenario === 'api-error') {
        await expect(page.getByRole('alert')).toContainText('503')
        await expect(kpi).toHaveText('完成率 --')
        await expect(charts.getByText('数据加载失败', { exact: true })).toHaveCount(10)
      } else {
        await expect(page.locator('#dashboard-warning')).toContainText('图表组件加载失败')
        await expect(kpi).toHaveText('完成率 75%')
        await expect(charts.getByText('图表组件不可用', { exact: true })).toHaveCount(10)
        await expect(page.locator('#dashboard-error')).toBeHidden()
      }
      await page.waitForLoadState('load')
      expect(libraryRequests).toBe(1)
      expect(summaryRequests).toBe(1)
      expect(external).toEqual([])
      expect(errors).toEqual([])
      await page.screenshot({ path: path.join(artifacts, `console-charts-${scenario}-${width}.png`), fullPage: true })
      console.log(`PASS ${scenario} ${width}px · ${await kpi.innerText()} · no public requests`)
    } finally {
      releaseLibrary()
      await page.close()
    }
  }
} finally {
  await browser.close()
}

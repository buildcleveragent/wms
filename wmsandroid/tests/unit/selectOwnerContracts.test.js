import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const pageSource = fs.readFileSync(
  path.resolve(import.meta.dirname, '../../pages/inbound/createwithoutorder/selectowner.vue'),
  'utf8',
)

describe('no-order owner selection contracts', () => {
  it('imports every lifecycle hook that it registers', () => {
    expect(pageSource).toMatch(
      /import\s*\{[^}]*onLoad[^}]*onReachBottom[^}]*onUnload[^}]*\}\s*from\s*['"]@dcloudio\/uni-app['"]/,
    )
  })

  it('defines the scanner callback before registering the shared scanner', () => {
    const callback = pageSource.indexOf('async function handleBarcodeScanned')
    const registration = pageSource.indexOf('useBarcodeScanner({ onScan: handleBarcodeScanned })')
    expect(callback).toBeGreaterThan(-1)
    expect(registration).toBeGreaterThan(callback)
  })

  it('renders loading, empty, and error states instead of failing silently', () => {
    expect(pageSource).toContain('data-testid="owner-loading"')
    expect(pageSource).toContain('data-testid="owner-empty"')
    expect(pageSource).toContain('data-testid="owner-error"')
    expect(pageSource).toContain("loadError.value = e?.message || '货主加载失败，请重试'")
  })
})

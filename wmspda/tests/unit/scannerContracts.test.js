import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const root = path.resolve(import.meta.dirname, '../..')

function sourceFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name)
    if (entry.isDirectory()) return entry.name === 'sample' ? [] : sourceFiles(target)
    return /\.(vue|js|nvue)$/.test(entry.name) ? [target] : []
  })
}

describe('scanner source contracts', () => {
  it('keeps native receiver registration in the shared scanner only', () => {
    const offenders = sourceFiles(path.join(root, 'pages'))
      .filter((file) => !file.endsWith('utils/useBarcodeScanner.js'))
      .filter((file) => fs.readFileSync(file, 'utf8').includes('.registerReceiver('))
    expect(offenders).toEqual([])
  })

  it('does not combine lastScan watchers with scanner callbacks on pages', () => {
    const offenders = sourceFiles(path.join(root, 'pages')).filter((file) => {
      const source = fs.readFileSync(file, 'utf8')
      return source.includes('watch(lastScan') && /onScan\s*:|setScanCallback/.test(source)
    })
    expect(offenders).toEqual([])
  })
})

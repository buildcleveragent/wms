import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { canAccessPath } from '@/utils/mobileAccess'

const root = path.resolve(import.meta.dirname, '../..')
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8')

describe('mobile app contracts', () => {
  it('registers only the mobile workbench, operations and profile tabs', () => {
    const pages = read('pages.json')
    expect(pages).toContain('"text": "工作台"')
    expect(pages).toContain('"text": "作业"')
    expect(pages).toContain('"text": "我的"')
    expect(pages).not.toContain('pages/reports')
    expect(pages).not.toContain('pages/pos/report')
  })

  it('uses camera barcode module and independent Android identity without PDA plugins', () => {
    const manifest = read('manifest.json')
    expect(manifest).toContain('com.jinqiaorongtong.wmsandroid')
    expect(manifest).toContain('"Barcode": {}')
    // Barcode injects its camera permission and hardware features during cloud
    // packaging. Adding any of them manually makes HBuilderX 5.23 emit duplicates.
    expect(manifest).not.toMatch(/android\.permission\.(CAMERA|FLASHLIGHT)/)
    expect(manifest).not.toContain('<uses-feature')
    expect(manifest).not.toContain('TH-PlatformSDK')
    expect(manifest).not.toMatch(/READ_PHONE_STATE|GET_ACCOUNTS|WRITE_SETTINGS|BLUETOOTH|NFC/)
    const networkConfig = read('nativeResources/android/res/xml/network_security_config.xml')
    expect(networkConfig).toContain('cleartextTrafficPermitted="false"')
    expect(networkConfig).not.toContain('192.168.')
    expect(read('vite.config.js')).toContain('wmsandroid-network-security-config')
  })

  it('gates POS and after-sales separately', () => {
    const base = { access: 'token', profileLoaded: true, profile: { capabilities: {} }, user: { warehouse_id: 1 }, perms: [] }
    expect(canAccessPath({ ...base, perms: ['pos.add_possale'] }, '/pages/pos/index')).toBe(true)
    expect(canAccessPath(base, '/pages/pos/index')).toBe(false)
    expect(canAccessPath({ ...base, perms: ['pos.view_possale'] }, '/pages/pos/history')).toBe(true)
    expect(canAccessPath({ ...base, perms: ['pos.add_possale'] }, '/pages/pos/history')).toBe(false)
    expect(canAccessPath({ ...base, perms: ['tasking.view_wmstask'] }, '/pages/picking/task_list')).toBe(true)
    expect(canAccessPath(base, '/pages/picking/task_list')).toBe(false)
  })
})

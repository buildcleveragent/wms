import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SCAN_FAILURE, ScanError, explainScanError, getCameraAuthorization, normalizeScanResult, scanOnce } from '@/utils/cameraScanner'

describe('camera scanner', () => {
  beforeEach(() => {
    vi.stubGlobal('uni', {
      scanCode: vi.fn(), vibrateShort: vi.fn(),
      getAppAuthorizeSetting: vi.fn(() => ({ cameraAuthorized: 'denied' })),
      getStorageSync: vi.fn(() => ''), setStorageSync: vi.fn(),
      showModal: vi.fn((options) => options.success({ confirm: true })),
      openAppAuthorizeSetting: vi.fn((options) => options.success()),
    })
  })

  it('normalizes code and format', () => {
    expect(normalizeScanResult({ result: ' 690123 ', scanType: 'EAN_13' })).toEqual({ rawValue: '690123', format: 'EAN_13' })
    expect(() => normalizeScanResult({ result: ' ' })).toThrow(ScanError)
  })

  it('opens only one active camera request and enables supported formats', async () => {
    let finish
    uni.scanCode.mockImplementation((options) => { finish = options.success })
    const first = scanOnce()
    const second = scanOnce()
    expect(first).toBe(second)
    expect(uni.scanCode).toHaveBeenCalledOnce()
    expect(uni.scanCode.mock.calls[0][0]).toMatchObject({ onlyFromCamera: true, autoZoom: true, scanType: ['barCode', 'qrCode', 'datamatrix'] })
    finish({ result: 'A-1', scanType: 'CODE_128' })
    await expect(first).resolves.toEqual({ rawValue: 'A-1', format: 'CODE_128' })
  })

  it('classifies cancelled scans without returning a fake empty barcode', async () => {
    uni.scanCode.mockImplementation((options) => options.fail({ errMsg: 'scanCode:fail cancel' }))
    await expect(scanOnce()).rejects.toMatchObject({ kind: SCAN_FAILURE.CANCELLED })
    expect(getCameraAuthorization()).toBe('denied')
  })

  it('explains first denial before directing repeated denial to system settings', async () => {
    const error = new ScanError(SCAN_FAILURE.PERMISSION_DENIED, '没有相机权限')
    await explainScanError(error)
    expect(uni.setStorageSync).toHaveBeenCalledWith('wmsandroid:camera:denial-explained', true)
    expect(uni.showModal.mock.calls[0][0]).toMatchObject({ title: '为什么需要相机', showCancel: false })
    expect(uni.openAppAuthorizeSetting).not.toHaveBeenCalled()

    uni.getStorageSync.mockReturnValue(true)
    await explainScanError(error)
    expect(uni.showModal.mock.calls[1][0]).toMatchObject({ title: '需要相机权限', confirmText: '打开设置' })
    expect(uni.openAppAuthorizeSetting).toHaveBeenCalledOnce()
  })
})

export const SCAN_FAILURE = Object.freeze({
  CANCELLED: 'cancelled',
  PERMISSION_DENIED: 'permission-denied',
  UNAVAILABLE: 'unavailable',
  FAILED: 'failed',
})

export class ScanError extends Error {
  constructor(kind, message, cause = null) {
    super(message)
    this.name = 'ScanError'
    this.kind = kind
    this.cause = cause
  }
}

let activeScan = null
const CAMERA_DENIAL_EXPLAINED_KEY = 'wmsandroid:camera:denial-explained'

function classifyScanFailure(error) {
  const text = String(error?.errMsg || error?.message || error || '').toLowerCase()
  if (text.includes('cancel')) return new ScanError(SCAN_FAILURE.CANCELLED, '已取消扫码', error)
  if (
    text.includes('permission') || text.includes('authorize') ||
    text.includes('auth deny') || text.includes('camera unauthorized')
  ) return new ScanError(SCAN_FAILURE.PERMISSION_DENIED, '没有相机权限', error)
  if (text.includes('not support') || text.includes('unavailable')) {
    return new ScanError(SCAN_FAILURE.UNAVAILABLE, '当前设备不支持相机扫码', error)
  }
  return new ScanError(SCAN_FAILURE.FAILED, '未识别到条码，请重试', error)
}

export function normalizeScanResult(result = {}) {
  const rawValue = String(result.result || result.rawValue || '').trim()
  if (!rawValue) throw new ScanError(SCAN_FAILURE.FAILED, '未识别到条码，请重试')
  return {
    rawValue,
    format: String(result.scanType || result.format || 'unknown').trim() || 'unknown',
  }
}

export function getCameraAuthorization() {
  try {
    return uni.getAppAuthorizeSetting?.()?.cameraAuthorized || 'unknown'
  } catch (error) {
    return 'unknown'
  }
}

export function openCameraSettings() {
  return new Promise((resolve) => {
    if (typeof uni.openAppAuthorizeSetting !== 'function') return resolve(false)
    uni.openAppAuthorizeSetting({ success: () => resolve(true), fail: () => resolve(false) })
  })
}

export function scanOnce(options = {}) {
  if (activeScan) return activeScan
  activeScan = new Promise((resolve, reject) => {
    if (typeof uni === 'undefined' || typeof uni.scanCode !== 'function') {
      reject(new ScanError(SCAN_FAILURE.UNAVAILABLE, '当前环境不支持相机扫码'))
      return
    }
    uni.scanCode({
      onlyFromCamera: true,
      autoZoom: true,
      scanType: options.scanTypes || ['barCode', 'qrCode', 'datamatrix'],
      success: (result) => {
        try {
          const normalized = normalizeScanResult(result)
          uni.vibrateShort?.({ type: 'light' })
          resolve(normalized)
        } catch (error) {
          reject(error)
        }
      },
      fail: (error) => reject(classifyScanFailure(error)),
    })
  }).finally(() => { activeScan = null })
  return activeScan
}

export async function explainScanError(error) {
  if (!(error instanceof ScanError) || error.kind === SCAN_FAILURE.CANCELLED) return false
  if (error.kind === SCAN_FAILURE.PERMISSION_DENIED) {
    let explained = false
    try { explained = Boolean(uni.getStorageSync?.(CAMERA_DENIAL_EXPLAINED_KEY)) } catch (storageError) {}
    if (!explained) {
      try { uni.setStorageSync?.(CAMERA_DENIAL_EXPLAINED_KEY, true) } catch (storageError) {}
      return new Promise((resolve) => {
        uni.showModal({
          title: '为什么需要相机',
          content: '扫码收银和仓库作业需要使用摄像头识别商品、库位和容器条码。应用不会保存拍摄画面。',
          showCancel: false,
          confirmText: '我知道了',
          success: () => resolve(false),
          fail: () => resolve(false),
        })
      })
    }
  }
  if (error.kind === SCAN_FAILURE.PERMISSION_DENIED || getCameraAuthorization() === 'denied') {
    return new Promise((resolve) => {
      uni.showModal({
        title: '需要相机权限',
        content: '手机扫码需要使用摄像头。请在系统设置中允许本应用使用相机。',
        confirmText: '打开设置',
        cancelText: '暂不设置',
        success: async (result) => {
          if (result.confirm) await openCameraSettings()
          resolve(result.confirm)
        },
        fail: () => resolve(false),
      })
    })
  }
  uni.showToast({ title: error.message, icon: 'none' })
  return false
}

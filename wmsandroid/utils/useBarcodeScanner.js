import { ref } from 'vue'
import { explainScanError, scanOnce } from '@/utils/cameraScanner'

// 兼容保留页面的旧组合式 API 名称，实现已切换为普通手机摄像头单次扫码。
export function useBarcodeScanner({ onScan } = {}) {
  const lastScan = ref('')
  const canScan = ref(typeof uni !== 'undefined' && typeof uni.scanCode === 'function')
  const scanning = ref(false)

  const quickScan = async () => {
    if (scanning.value) return null
    scanning.value = true
    try {
      const result = await scanOnce()
      lastScan.value = result.rawValue
      if (typeof onScan === 'function') await onScan(result.rawValue, result)
      return result
    } catch (error) {
      await explainScanError(error)
      return null
    } finally {
      scanning.value = false
    }
  }

  return { lastScan, canScan, scanning, quickScan }
}

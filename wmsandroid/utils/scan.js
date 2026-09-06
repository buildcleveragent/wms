import { explainScanError, scanOnce } from '@/utils/cameraScanner'

export async function scanOne() {
  try {
    return (await scanOnce()).rawValue
  } catch (error) {
    await explainScanError(error)
    return ''
  }
}

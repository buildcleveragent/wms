export function isScanCancellation(error) {
  const message = String(error?.errMsg || error?.message || '').toLowerCase()
  return message.includes('cancel') || message.includes('取消')
}

export async function scanOne() {
  try {
    const response = await new Promise((resolve, reject) => {
      uni.scanCode({ onlyFromCamera: true, success: resolve, fail: reject })
    })
    return String(response?.result || '').trim()
  } catch (error) {
    if (isScanCancellation(error)) return ''
    throw error
  }
}

import { BASE_URL, getToken } from '@/utils/request'

export function prepareOutboundPrintWindow() {
  let preparedWindow = null
  // #ifdef H5
  if (typeof window !== 'undefined' && typeof window.open === 'function') {
    try {
      preparedWindow = window.open('', '_blank', 'width=1200,height=800')
    } catch (error) {
      console.warn('无法预先打开出库单打印窗口', error)
    }
  }
  // #endif
  return preparedWindow
}

export function closePreparedOutboundPrintWindow(preparedWindow) {
  if (!preparedWindow || preparedWindow.closed) return
  try {
    preparedWindow.close()
  } catch (error) {
    console.warn('无法关闭未使用的出库单打印窗口', error)
  }
}

export function outboundPrintUrl(taskId) {
  const token = getToken()
  if (!token || !Number(taskId)) return ''
  return `${BASE_URL}/api/pda/pick-tasks/${Number(taskId)}/print/?token=${encodeURIComponent(token)}`
}

export function openOutboundPrintPage(taskId, preparedWindow = null) {
  const url = outboundPrintUrl(taskId)
  if (!url) {
    closePreparedOutboundPrintWindow(preparedWindow)
    uni.showToast({ title: '登录已失效，暂时无法打印出库单', icon: 'none' })
    return false
  }

  let opened = false
  // #ifdef H5
  const printWindow = preparedWindow && !preparedWindow.closed
    ? preparedWindow
    : prepareOutboundPrintWindow()
  if (printWindow) {
    printWindow.location.href = url
    opened = true
  } else {
    uni.showToast({ title: '浏览器阻止打印窗口，请稍后从历史出库单重试', icon: 'none' })
  }
  // #endif

  // #ifdef APP-PLUS
  try {
    plus.runtime.openURL(url)
    opened = true
  } catch (error) {
    console.warn('无法打开出库单打印页', error)
    uni.showToast({ title: '当前设备无法打开出库单打印页，请稍后重试', icon: 'none' })
  }
  // #endif
  return opened
}

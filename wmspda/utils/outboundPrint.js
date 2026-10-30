import { BASE_URL, getToken, withAuthToken } from '@/utils/request'

export function prepareOutboundPrintWindow() {
  let preparedWindow = null
  // #ifdef H5
  if (typeof window !== 'undefined' && typeof window.open === 'function') {
    try {
      preparedWindow = window.open('', '_blank', 'width=1200,height=800')
      if (preparedWindow) preparedWindow.document.body.textContent = '正在处理，请稍候…成功后将打开打印单据。'
    } catch (error) {
      console.warn('无法预先打开送货单打印窗口', error)
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
    console.warn('无法关闭未使用的送货单打印窗口', error)
  }
}

export async function outboundPrintUrl(taskId, kind = 'delivery') {
  const token = getToken()
  if (!token || !Number(taskId)) return ''
  return withAuthToken(`${BASE_URL}/api/pda/pick-tasks/${Number(taskId)}/print/?kind=${kind}`)
}

export async function openOutboundPrintPage(taskId, preparedWindow = null, kind = 'delivery') {
  preparedWindow = preparedWindow || prepareOutboundPrintWindow()
  let url
  try { url = await outboundPrintUrl(taskId, kind) } catch (error) {
    closePreparedOutboundPrintWindow(preparedWindow)
    uni.showToast({ title: '无法取得打印票据，请稍后重试', icon: 'none' })
    return false
  }
  if (!url) {
    closePreparedOutboundPrintWindow(preparedWindow)
    uni.showToast({ title: '登录已失效，暂时无法打印送货单', icon: 'none' })
    return false
  }

  let opened = false
  // #ifdef H5
  const printWindow = preparedWindow && !preparedWindow.closed
    ? preparedWindow
    : prepareOutboundPrintWindow()
  if (printWindow) {
    try {
      printWindow.location.href = url
      opened = true
    } catch (error) {
      closePreparedOutboundPrintWindow(printWindow)
      uni.showToast({ title: '打印窗口加载失败，可点击补打送货单重试', icon: 'none' })
    }
  } else {
    uni.showToast({ title: '浏览器阻止打印窗口，请点击打印按钮重试', icon: 'none' })
  }
  // #endif

  // #ifdef APP-PLUS
  try {
    plus.runtime.openURL(url)
    opened = true
  } catch (error) {
    console.warn('无法打开送货单打印页', error)
    uni.showToast({ title: '当前设备无法打开送货单打印页，请稍后重试', icon: 'none' })
  }
  // #endif
  return opened
}

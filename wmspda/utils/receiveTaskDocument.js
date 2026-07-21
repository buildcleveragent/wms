import { BASE_URL, getToken } from '@/utils/request'

export function openReceiveTaskPrint(taskId) {
  const token = getToken()
  if (!token) {
    uni.showToast({ title: '登录已失效，请重新登录', icon: 'none' })
    return
  }
  const url = `${BASE_URL}/api/inbound/receive_task/${taskId}/print/?token=${encodeURIComponent(token)}`

  // #ifdef H5
  window.open(url, '_blank')
  // #endif

  // #ifdef APP-PLUS
  try {
    plus.runtime.openURL(url)
  } catch (error) {
    uni.showToast({ title: '当前环境不支持直接打印', icon: 'none' })
  }
  // #endif
}

export function exportReceiveTaskExcel(taskId) {
  const token = getToken()
  if (!token) {
    return Promise.reject(new Error('登录已失效，请重新登录'))
  }
  const url = `${BASE_URL}/api/inbound/receive_task/${taskId}/export_excel/`

  // #ifdef H5
  if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
    return window.fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
    }).then(async (response) => {
      if (!response.ok) throw new Error('收货单导出失败')
      const blob = await response.blob()
      const objectUrl = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = `收货单-${taskId}.xlsx`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.URL.revokeObjectURL(objectUrl)
      return { opened: true }
    })
  }
  // #endif

  return new Promise((resolve, reject) => {
    uni.downloadFile({
      url,
      header: { Authorization: `Bearer ${token}` },
      success: (res) => {
        if (res.statusCode !== 200) {
          reject(new Error('收货单导出失败'))
          return
        }
        uni.openDocument({
          filePath: res.tempFilePath,
          fileType: 'xlsx',
          showMenu: true,
          success: resolve,
          fail: reject,
        })
      },
      fail: reject,
    })
  })
}

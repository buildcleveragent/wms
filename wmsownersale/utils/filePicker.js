const MAX_EXCEL_SIZE = 5 * 1024 * 1024

export class FilePickerError extends Error {
  constructor(code, message) {
    super(message)
    this.name = 'FilePickerError'
    this.code = code
  }
}

export function isPickerCancellation(error) {
  const message = String(error?.errMsg || error?.message || '').toLowerCase()
  return message.includes('cancel') || message.includes('取消')
}

export function normalizePickerFailure(error) {
  return isPickerCancellation(error)
    ? null
    : new FilePickerError('picker_failed', '选择文件失败，请重试')
}

export function normalizeExcelFileResponse(response) {
  const file = response?.tempFiles?.[0] || {}
  const path = file.path || response?.tempFilePaths?.[0] || ''
  const name = file.name || path.split('/').pop() || ''
  const size = Number(file.size || 0)

  if (!path) throw new FilePickerError('empty_file', '没有读取到所选文件')
  if (!name.toLowerCase().endsWith('.xlsx')) {
    throw new FilePickerError('invalid_extension', '仅支持 .xlsx 格式')
  }
  if (size > MAX_EXCEL_SIZE) {
    throw new FilePickerError('file_too_large', 'Excel 文件不能超过 5 MB')
  }
  return { path, name, size }
}

function pickerPromise(start) {
  return new Promise((resolve, reject) => {
    start({
      success: (response) => {
        try {
          resolve(normalizeExcelFileResponse(response))
        } catch (error) {
          reject(error)
        }
      },
      fail: (error) => {
        const normalized = normalizePickerFailure(error)
        if (!normalized) {
          resolve(null)
          return
        }
        reject(normalized)
      },
    })
  })
}

export function chooseExcelFile() {
  // #ifdef H5
  return pickerPromise((callbacks) => uni.chooseFile({
    count: 1,
    extension: ['.xlsx'],
    ...callbacks,
  }))
  // #endif

  // #ifdef MP-WEIXIN
  return pickerPromise((callbacks) => wx.chooseMessageFile({
    count: 1,
    type: 'file',
    extension: ['xlsx'],
    ...callbacks,
  }))
  // #endif

  return Promise.reject(new FilePickerError('unsupported_platform', '当前平台不支持选择 Excel 文件'))
}

const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

function normalizeChosenFile(path, file = {}) {
  const name = file.name || (path || '').split('/').pop() || '商品导入.xlsx'
  return {
    path: path || file.path || '',
    name,
    size: Number(file.size || 0),
  }
}

function chooseWithUniFile() {
  return new Promise((resolve, reject) => {
    uni.chooseFile({
      count: 1,
      extension: ['.xlsx'],
      success: (res) => {
        const path = res?.tempFilePaths?.[0] || res?.tempFiles?.[0]?.path || ''
        if (!path) {
          reject(new Error('没有取得所选文件路径'))
          return
        }
        resolve(normalizeChosenFile(path, res?.tempFiles?.[0] || {}))
      },
      fail: reject,
    })
  })
}

function chooseWithWechat() {
  return new Promise((resolve, reject) => {
    uni.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['xlsx'],
      success: (res) => {
        const file = res?.tempFiles?.[0] || {}
        if (!file.path) {
          reject(new Error('没有取得所选文件路径'))
          return
        }
        resolve(normalizeChosenFile(file.path, file))
      },
      fail: reject,
    })
  })
}

function androidDisplayName(resolver, uri) {
  let cursor = null
  try {
    cursor = plus.android.invoke(resolver, 'query', uri, null, null, null, null)
    if (!cursor || !plus.android.invoke(cursor, 'moveToFirst')) return ''
    const index = plus.android.invoke(cursor, 'getColumnIndex', '_display_name')
    return index >= 0 ? plus.android.invoke(cursor, 'getString', index) || '' : ''
  } catch (error) {
    return ''
  } finally {
    if (cursor) plus.android.invoke(cursor, 'close')
  }
}

function copyAndroidContentUri(activity, uri) {
  const resolver = plus.android.invoke(activity, 'getContentResolver')
  const input = plus.android.invoke(resolver, 'openInputStream', uri)
  if (!input) throw new Error('无法读取所选文件')

  const File = plus.android.importClass('java.io.File')
  const FileOutputStream = plus.android.importClass('java.io.FileOutputStream')
  const cacheDir = plus.android.invoke(activity, 'getCacheDir')
  const target = new File(cacheDir, `product-import-${Date.now()}.xlsx`)
  const output = new FileOutputStream(target)
  const buffer = plus.android.newObject('byte[]', 8192)
  try {
    let length = plus.android.invoke(input, 'read', buffer)
    while (length > 0) {
      plus.android.invoke(output, 'write', buffer, 0, length)
      length = plus.android.invoke(input, 'read', buffer)
    }
    plus.android.invoke(output, 'flush')
  } finally {
    plus.android.invoke(input, 'close')
    plus.android.invoke(output, 'close')
  }
  return {
    path: plus.android.invoke(target, 'getAbsolutePath'),
    size: Number(plus.android.invoke(target, 'length') || 0),
    name: androidDisplayName(resolver, uri) || '商品导入.xlsx',
  }
}

function chooseWithAndroidDocument() {
  return new Promise((resolve, reject) => {
    if (typeof plus === 'undefined' || !plus.android) {
      reject(new Error('当前设备不支持系统文件选择'))
      return
    }
    let activity = null
    let previousHandler = null
    try {
      activity = plus.android.runtimeMainActivity()
      const Intent = plus.android.importClass('android.content.Intent')
      const intent = new Intent('android.intent.action.OPEN_DOCUMENT')
      intent.addCategory('android.intent.category.OPENABLE')
      intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
      intent.setType(XLSX_MIME)

      const requestCode = 43127
      previousHandler = activity.onActivityResult
      activity.onActivityResult = (code, resultCode, data) => {
        if (code !== requestCode) {
          if (typeof previousHandler === 'function') previousHandler(code, resultCode, data)
          return
        }
        activity.onActivityResult = previousHandler
        if (resultCode !== -1 || !data) {
          reject(new Error('已取消选择文件'))
          return
        }
        try {
          const uri = plus.android.invoke(data, 'getData')
          resolve(copyAndroidContentUri(activity, uri))
        } catch (error) {
          reject(error)
        }
      }
      activity.startActivityForResult(intent, requestCode)
    } catch (error) {
      if (activity) activity.onActivityResult = previousHandler
      reject(error)
    }
  })
}

export function chooseExcelFile() {
  // #ifdef APP-PLUS
  return chooseWithAndroidDocument()
  // #endif

  // #ifdef MP-WEIXIN
  return chooseWithWechat()
  // #endif

  // #ifdef H5
  return chooseWithUniFile()
  // #endif

  return Promise.reject(new Error('当前平台暂不支持选择 Excel 文件'))
}

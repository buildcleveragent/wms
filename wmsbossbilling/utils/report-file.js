// App uses HTML5+; uni.getFileSystemManager/openDocument are mini-program APIs.
export async function saveAndroidReport(data, filename) {
  if (typeof plus === 'undefined' || plus.os?.name !== 'Android') {
    throw new Error('当前设备不支持保存报表，请使用 Android 或浏览器导出')
  }
  const filePath = `_doc/${filename}`
  const absolute = plus.io.convertLocalFileSystemURL(filePath)
  const bytes = plus.android.invoke('android.util.Base64', 'decode', uni.arrayBufferToBase64(data), 0)
  if (!bytes) throw new Error('报表数据转换失败')
  const output = plus.android.newObject('java.io.FileOutputStream', absolute)
  if (!output) throw new Error('无法保存报表，请检查设备存储空间')
  try {
    plus.android.invoke(output, 'write', bytes)
  } finally {
    plus.android.invoke(output, 'close')
  }
  // Native.js can return undefined instead of throwing on a failed invocation.
  const info = await new Promise((resolve, reject) => {
    plus.io.getFileInfo({ filePath, success: resolve, fail: reject })
  })
  if (info.size !== data.byteLength) throw new Error('报表文件写入不完整，请重试')
  plus.runtime.openFile(filePath, {}, () => {
    uni.showToast({ title: '文件已保存，请安装支持 Excel 的应用后打开', icon: 'none', duration: 4000 })
  })
  return filePath
}

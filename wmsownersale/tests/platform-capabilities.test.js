import { describe, expect, it, vi } from 'vitest'

import {
  chooseExcelFile,
  FilePickerError,
  isPickerCancellation,
  normalizeExcelFileResponse,
  normalizePickerFailure,
} from '@/utils/filePicker'
import { isScanCancellation, scanOne } from '@/utils/scan'

describe('Excel 文件选择归一化', () => {
  it('H5 通过 uni.chooseFile 选择并归一化文件', async () => {
    const chooseFile = vi.fn(({ success }) => success({
      tempFilePaths: ['/tmp/orders.xlsx'],
      tempFiles: [{ name: 'orders.xlsx', size: 123 }],
    }))
    vi.stubGlobal('uni', { chooseFile })

    await expect(chooseExcelFile()).resolves.toEqual({
      path: '/tmp/orders.xlsx', name: 'orders.xlsx', size: 123,
    })
    expect(chooseFile).toHaveBeenCalledWith(expect.objectContaining({
      count: 1,
      extension: ['.xlsx'],
    }))
  })

  it('H5 取消不报错，系统失败返回明确错误', async () => {
    vi.stubGlobal('uni', {
      chooseFile: ({ fail }) => fail({ errMsg: 'chooseFile:fail cancel' }),
    })
    await expect(chooseExcelFile()).resolves.toBeNull()

    vi.stubGlobal('uni', {
      chooseFile: ({ fail }) => fail({ errMsg: 'chooseFile:fail permission denied' }),
    })
    await expect(chooseExcelFile()).rejects.toMatchObject({
      name: 'FilePickerError', code: 'picker_failed',
    })
  })

  it('兼容 H5 与微信文件响应', () => {
    expect(normalizeExcelFileResponse({
      tempFilePaths: ['/tmp/orders.xlsx'],
      tempFiles: [{ name: 'orders.xlsx', size: 123 }],
    })).toEqual({ path: '/tmp/orders.xlsx', name: 'orders.xlsx', size: 123 })

    expect(normalizeExcelFileResponse({
      tempFiles: [{ path: 'wxfile://orders.xlsx', name: 'orders.xlsx', size: 456 }],
    })).toEqual({ path: 'wxfile://orders.xlsx', name: 'orders.xlsx', size: 456 })
  })

  it('分别拒绝格式、大小和空文件', () => {
    expect(() => normalizeExcelFileResponse({
      tempFiles: [{ path: '/tmp/orders.xls', name: 'orders.xls', size: 1 }],
    })).toThrowError(expect.objectContaining({ code: 'invalid_extension' }))
    expect(() => normalizeExcelFileResponse({
      tempFiles: [{ path: '/tmp/orders.xlsx', name: 'orders.xlsx', size: 6 * 1024 * 1024 }],
    })).toThrowError(expect.objectContaining({ code: 'file_too_large' }))
    expect(() => normalizeExcelFileResponse({})).toThrow(FilePickerError)
  })

  it('把用户取消与系统错误区分开', () => {
    expect(isPickerCancellation({ errMsg: 'chooseMessageFile:fail cancel' })).toBe(true)
    expect(isPickerCancellation({ errMsg: 'chooseMessageFile:fail permission denied' })).toBe(false)
    expect(normalizePickerFailure({ errMsg: 'chooseFile:fail cancel' })).toBeNull()
    expect(normalizePickerFailure({ errMsg: 'chooseFile:fail permission denied' })).toMatchObject({
      name: 'FilePickerError',
      code: 'picker_failed',
    })
  })
})

describe('扫码错误处理', () => {
  it('用户取消返回空值', async () => {
    vi.stubGlobal('uni', {
      scanCode: ({ fail }) => fail({ errMsg: 'scanCode:fail cancel' }),
    })
    await expect(scanOne()).resolves.toBe('')
  })

  it('设备或权限错误继续向页面抛出', async () => {
    const error = { errMsg: 'scanCode:fail permission denied' }
    vi.stubGlobal('uni', { scanCode: ({ fail }) => fail(error) })
    expect(isScanCancellation(error)).toBe(false)
    await expect(scanOne()).rejects.toBe(error)
  })
})

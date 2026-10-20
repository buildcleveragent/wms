import { afterEach, beforeEach, expect, it, vi } from 'vitest'
vi.mock('@/store/cart',()=>({useCart:()=>({resetOrder:vi.fn()})}))
let storage, click
beforeEach(()=>{
  vi.resetModules();vi.useFakeTimers();storage={access:'a',refresh:'r'}
  vi.stubGlobal('uni',{
    getAccountInfoSync:()=>({miniProgram:{envVersion:'develop'}}),
    getStorageSync:k=>storage[k] || '',setStorageSync:(k,v)=>{storage[k]=v},removeStorageSync:k=>{delete storage[k]},
    request:vi.fn(),showToast:vi.fn(),reLaunch:vi.fn(),
  })
  URL.createObjectURL=vi.fn(()=> 'blob:excel');URL.revokeObjectURL=vi.fn()
  click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{})
})
afterEach(()=>{vi.clearAllTimers();vi.useRealTimers();vi.unstubAllGlobals()})
const bytes=text=>new TextEncoder().encode(text).buffer

it('decodes binary API failures instead of downloading error files',async()=>{
  uni.request.mockImplementation(o=>o.success({statusCode:413,data:bytes('{"detail":"请缩小筛选范围"}')}))
  const {downloadAuthenticatedFile}=await import('@/utils/request')
  await expect(downloadAuthenticatedFile('/export/','test.xlsx',{excel:true})).rejects.toMatchObject({statusCode:413,message:'请缩小筛选范围'})
  expect(click).not.toHaveBeenCalled()
})
it('validates workbook bytes and downloads only the current request',async()=>{
  uni.request.mockImplementation(o=>o.success({statusCode:200,data:bytes('PKtest')}))
  const {downloadAuthenticatedFile}=await import('@/utils/request')
  await downloadAuthenticatedFile('/export/','订单.xlsx',{excel:true,isCurrent:()=>false})
  expect(click).not.toHaveBeenCalled()
  await downloadAuthenticatedFile('/export/','订单.xlsx',{excel:true})
  expect(click).toHaveBeenCalledOnce()
  vi.advanceTimersByTime(1000);expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:excel')
})
it('rejects a success response containing JSON instead of a workbook',async()=>{
  uni.request.mockImplementation(o=>o.success({statusCode:200,data:bytes('{"detail":"导出不可用"}')}))
  const {downloadAuthenticatedFile}=await import('@/utils/request')
  await expect(downloadAuthenticatedFile('/export/','test.xlsx',{excel:true})).rejects.toMatchObject({message:'导出不可用'})
  expect(click).not.toHaveBeenCalled()
})
it('drops a completed file when the account changed during the request',async()=>{
  let finish;uni.request.mockImplementation(o=>{finish=o.success})
  const {downloadAuthenticatedFile,setTokens}=await import('@/utils/request')
  const pending=downloadAuthenticatedFile('/export/','test.xlsx',{excel:true}).catch(e=>e)
  setTokens('account-b','refresh-b');finish({statusCode:200,data:bytes('PKtest')})
  expect((await pending).statusCode).toBe(409);expect(click).not.toHaveBeenCalled()
})

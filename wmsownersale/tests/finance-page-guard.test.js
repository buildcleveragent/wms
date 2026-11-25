import { nextTick, reactive } from 'vue'
import { beforeEach, expect, it, vi } from 'vitest'

const auth = reactive({user:{id:1,owner_id:2},roles:['owner_finance'],capabilities:{},
  get isOwnerFinance() { return this.roles.includes('owner_finance') }})
vi.mock('@/store/auth', () => ({useAuth: () => auth}))
const cart = {resetOrder:vi.fn()}
vi.mock('@/store/cart', () => ({useCart: () => cart}))
import { installFinancePageGuard } from '@/utils/financePageGuard'

beforeEach(() => {
  vi.stubGlobal('uni', {redirectTo:vi.fn(),reLaunch:vi.fn()})
})

it('只读深链只跳转一次，角色切换清空缓存页面', async () => {
  let hooks
  installFinancePageGuard({mixin: value => { hooks=value }}, {})
  const page = {$page:{fullPath:'/pages/orders/cart'}}
  hooks.onLoad.call(page,{})
  hooks.onShow.call(page)
  expect(uni.redirectTo).toHaveBeenCalledTimes(1)
  expect(uni.redirectTo).toHaveBeenCalledWith(expect.objectContaining({url:'/pages/read-only'}))
  auth.roles=['owner_manager']
  await nextTick()
  expect(uni.reLaunch).toHaveBeenCalledWith({url:'/pages/home/index'})
  expect(cart.resetOrder).toHaveBeenCalledTimes(1)
  hooks.onLoad.call({route:'pages/orders/cart'}, {})
  expect(uni.redirectTo).toHaveBeenCalledTimes(1)
})

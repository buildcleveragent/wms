import { watch } from 'vue'
import { useAuth } from '@/store/auth'
import { useCart } from '@/store/cart'
import { financeIdentity, financePageBlocked } from './financeAccess'

export function installFinancePageGuard(app, pinia) {
  const auth = useAuth(pinia)
  const redirected = new WeakSet()
  function checkPage(page, options) {
    if (!auth.isOwnerFinance || redirected.has(page)) return
    const route = page?.route || page?.$page?.route || page?.$page?.fullPath || ''
    if (financePageBlocked(route, options || page?.options || page?.$page?.options || {})) {
      redirected.add(page)
      uni.redirectTo({ url: '/pages/read-only', fail() {} })
    }
  }
  app.mixin({
    onLoad(options) { checkPage(this, options) },
    onShow() { checkPage(this) },
  })
  watch(() => financeIdentity(auth), (value, previous) => {
    const before = JSON.parse(previous), after = JSON.parse(value)
    if (before[0] && after[0] &&
        (before[2].includes('owner_finance') || after[2].includes('owner_finance'))) {
      // Discard cached pages, late responses and editing state on identity changes.
      useCart(pinia).resetOrder()
      uni.reLaunch({ url: '/pages/home/index' })
    }
  })
}

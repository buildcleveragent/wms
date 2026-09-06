import { createSSRApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { useAuth } from '@/store/auth'
import { guardCurrentPage } from '@/utils/mobileAccess'

export function createApp() {
  const app = createSSRApp(App)
  const pinia = createPinia()
  app.use(pinia)
  app.mixin({
    onShow() {
      guardCurrentPage(useAuth(pinia))
    },
  })
  return { app }
}

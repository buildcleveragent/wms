import { createSSRApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { installFinancePageGuard } from './utils/financePageGuard'


export function createApp() {
const app = createSSRApp(App)
const pinia = createPinia()
app.use(pinia)
installFinancePageGuard(app, pinia)
return { app }
}

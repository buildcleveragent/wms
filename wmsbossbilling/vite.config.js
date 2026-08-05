import { defineConfig, loadEnv } from 'vite'
import uni from '@dcloudio/vite-plugin-uni'

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiBase = String(env.VITE_API_BASE_URL || '').trim()
  const platform = process.env.UNI_PLATFORM || 'h5'

  if (command === 'build') {
    if (platform !== 'h5' && !apiBase) {
      throw new Error('VITE_API_BASE_URL is required for App and mini-program production builds.')
    }
    if (apiBase && !/^https:\/\//i.test(apiBase)) {
      throw new Error('Production VITE_API_BASE_URL must use HTTPS.')
    }
  }

  return { plugins: [uni()] }
})

import { defineConfig, loadEnv } from 'vite'
import uni from '@dcloudio/vite-plugin-uni'
import { validateApiBase, validateWeixinManifest } from '../frontend/api-base-policy.mjs'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const platform = process.env.UNI_PLATFORM || 'h5'
  validateApiBase({
    value: env.VITE_API_BASE_URL,
    platform,
    mode,
    allowPrivate: env.VITE_ALLOW_PRIVATE_API === 'true',
  })
  validateWeixinManifest({ platform })
  return { plugins: [uni()] }
})

import { defineConfig, loadEnv } from 'vite'
import { fileURLToPath } from 'node:url'
import uni from '@dcloudio/vite-plugin-uni'
import { validateApiBase, validateWeixinManifest } from '../frontend/api-base-policy.mjs'

export default defineConfig(({ mode }) => {
  // HBuilderX may launch Vite from its own installation directory.
  const projectDir = fileURLToPath(new URL('.', import.meta.url))
  const env = loadEnv(mode, projectDir, '')
  const platform = process.env.UNI_PLATFORM || 'h5'
  validateApiBase({
    value: env.VITE_API_BASE_URL,
    platform,
    mode,
    allowPrivate: env.VITE_ALLOW_PRIVATE_API === 'true',
  })
  validateWeixinManifest({ platform, root: projectDir })
  return {
    envDir: projectDir,
    plugins: [uni()],
    server: {
      proxy: {
        '/api/': {
          target: env.WMS_DEV_API_TARGET || 'http://127.0.0.1:8001',
          changeOrigin: true,
          proxyTimeout: 15000,
        },
      },
    },
  }
})

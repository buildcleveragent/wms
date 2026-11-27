import localH5Assets from '../frontend/local-h5-assets.mjs'
import { defineConfig, loadEnv } from 'vite'
import { fileURLToPath } from 'node:url'
import uni from '@dcloudio/vite-plugin-uni'
import { validateWeixinManifest } from '../frontend/api-base-policy.mjs'

export default defineConfig(({ mode }) => {
  const projectDir = fileURLToPath(new URL('.', import.meta.url))
  const apiBase = (loadEnv(mode, projectDir, 'VITE_').VITE_API_BASE_URL || '').trim()
  if (apiBase) {
    const url = URL.parse(apiBase)
    if (!url || !['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
        /[?#\\\s]/.test(apiBase) || !/^https?:\/\//i.test(apiBase)) {
      throw new Error('VITE_API_BASE_URL 必须是无凭据、查询参数和片段的 HTTP(S) 地址。')
    }
  }
  const platform = process.env.UNI_PLATFORM || 'h5'
  validateWeixinManifest({ platform, root: projectDir })
  return {
    envDir: projectDir,
    plugins: [uni()],
    css: { postcss: { plugins: platform === 'h5' ? [localH5Assets()] : [] } },
  }
})

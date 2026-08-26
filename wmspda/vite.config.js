import { defineConfig } from 'vite'
import uni from '@dcloudio/vite-plugin-uni'
import { validateWeixinManifest } from '../frontend/api-base-policy.mjs'

export default defineConfig(() => {
  const platform = process.env.UNI_PLATFORM || 'h5'
  validateWeixinManifest({ platform })
  return { plugins: [uni()] }
})

import { defineConfig, loadEnv } from 'vite'
import { fileURLToPath } from 'node:url'
import { copyFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import uni from '@dcloudio/vite-plugin-uni'
import { validateWeixinManifest } from '../frontend/api-base-policy.mjs'
import { validateOwnerApiBase } from './utils/api-base-policy.js'
import { renderAndroidNetworkSecurityConfig } from './config/androidNetworkSecurity.mjs'

export default defineConfig(({ mode }) => {
  // HBuilderX may launch Vite from its own installation directory.
  const projectDir = fileURLToPath(new URL('.', import.meta.url))
  const platform = process.env.UNI_PLATFORM || 'h5'
  const appPlatform = process.env.UNI_APP_PLATFORM || 'android'
  const android = ['app', 'app-plus'].includes(platform) && appPlatform === 'android'
  const envDir = android ? resolve(projectDir, 'config/android') : projectDir
  const env = loadEnv(mode, envDir, '')
  const options = {
    value: env.VITE_API_BASE_URL,
    platform,
    appPlatform,
    mode,
    allowPrivate: env.VITE_ALLOW_PRIVATE_API === 'true',
    allowHttp: env.VITE_ALLOW_HTTP_API === 'true',
  }
  const apiBase = validateOwnerApiBase(options)
  validateWeixinManifest({ platform, root: projectDir })
  return {
    envDir,
    define: {
      'import.meta.env.WMS_BUILD_PLATFORM': JSON.stringify(platform),
      'import.meta.env.WMS_APP_PLATFORM': JSON.stringify(appPlatform),
      'import.meta.env.VITE_API_BASE_URL': JSON.stringify(apiBase),
    },
    plugins: [{
      name: 'ownersale-android-network-security',
      buildStart() {
        if (!android) return
        const target = resolve(projectDir, 'nativeResources/android/res/xml/network_security_config.xml')
        mkdirSync(dirname(target), { recursive: true })
        writeFileSync(target, renderAndroidNetworkSecurityConfig(options))
      },
      writeBundle() {
        if (!android || !process.env.UNI_OUTPUT_DIR) return
        const output = resolve(process.env.UNI_OUTPUT_DIR)
        const nativeDir = resolve(output, 'nativeResources/android/res/xml')
        mkdirSync(nativeDir, { recursive: true })
        writeFileSync(resolve(nativeDir, 'network_security_config.xml'), renderAndroidNetworkSecurityConfig(options))
        copyFileSync(resolve(projectDir, 'AndroidManifest.xml'), resolve(output, 'AndroidManifest.xml'))
      },
    }, uni()],
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

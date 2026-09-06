import { defineConfig, loadEnv } from 'vite'
import uni from '@dcloudio/vite-plugin-uni'
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { renderAndroidNetworkSecurityConfig } from './config/androidNetworkSecurity.mjs'

const CONFIG_DIRECTORY = fileURLToPath(new URL('.', import.meta.url))
const PROJECT_ROOT = resolve(process.env.UNI_INPUT_DIR || CONFIG_DIRECTORY)
const LOCAL_ENV_PATH = resolve(PROJECT_ROOT, '.env')
const NETWORK_CONFIG_PATH = resolve(
  PROJECT_ROOT,
  'nativeResources/android/res/xml/network_security_config.xml',
)

function androidNetworkSecurityPlugin({ platform, apiBaseUrl }) {
  const isAndroidBuild = ['app', 'app-plus'].includes(platform)
  return {
    name: 'wmsandroid-network-security-config',
    buildStart() {
      if (!isAndroidBuild) return
      writeFileSync(
        NETWORK_CONFIG_PATH,
        renderAndroidNetworkSecurityConfig(apiBaseUrl),
        'utf8',
      )
    },
  }
}

export default defineConfig(({ mode }) => {
  const platform = process.env.UNI_PLATFORM || 'h5'
  const env = loadEnv(mode, PROJECT_ROOT, '')
  const apiBaseUrl = String(
    process.env.VITE_API_BASE_URL !== undefined
      ? process.env.VITE_API_BASE_URL
      : env.VITE_API_BASE_URL || '',
  ).trim()
  if (
    ['app', 'app-plus'].includes(platform) &&
    !apiBaseUrl
  ) {
    throw new Error(`Android App 构建必须在 ${LOCAL_ENV_PATH} 配置 VITE_API_BASE_URL`)
  }
  return {
    root: PROJECT_ROOT,
    envDir: PROJECT_ROOT,
    plugins: [
      androidNetworkSecurityPlugin({ platform, apiBaseUrl }),
      uni(),
    ],
  }
})

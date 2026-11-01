import { validateBossApiBase } from './api-base-policy.mjs'

export const BASE_URL = validateBossApiBase({
  value: import.meta.env.VITE_API_BASE_URL,
  platform: import.meta.env.WMS_BUILD_PLATFORM || 'h5',
  mode: import.meta.env.DEV ? 'development' : 'production',
  allowPrivate: import.meta.env.VITE_ALLOW_PRIVATE_API === 'true',
  allowHttp: import.meta.env.VITE_ALLOW_HTTP_API === 'true',
})

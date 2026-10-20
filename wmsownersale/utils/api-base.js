import { validateOwnerApiBase } from './api-base-policy.js'

export const BASE_URL = validateOwnerApiBase({
  value: import.meta.env.VITE_API_BASE_URL,
  platform: import.meta.env.WMS_BUILD_PLATFORM || 'h5',
  appPlatform: import.meta.env.WMS_APP_PLATFORM || 'android',
  mode: import.meta.env.DEV ? 'development' : 'production',
  allowPrivate: import.meta.env.VITE_ALLOW_PRIVATE_API === 'true',
  allowHttp: import.meta.env.VITE_ALLOW_HTTP_API === 'true',
})

import { validateBossApiBase } from '../utils/api-base-policy.mjs'

export function buildH5DevProxy({ platform, mode, target }) {
  if (platform !== 'h5' || mode !== 'development' || !String(target || '').trim()) return undefined
  const origin = validateBossApiBase({
    value: target,
    platform: 'h5',
    mode: 'development',
    allowHttp: true,
    allowPrivate: true,
  })
  return {
    '/api/': {
      target: origin,
      changeOrigin: true,
      proxyTimeout: 15000,
    },
  }
}

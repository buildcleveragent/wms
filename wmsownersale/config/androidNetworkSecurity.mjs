import { validateOwnerApiBase } from '../utils/api-base-policy.js'

export function renderAndroidNetworkSecurityConfig(options) {
  const base = validateOwnerApiBase({ ...options, platform: 'app', appPlatform: 'android' })
  const url = new URL(base)
  // HBuilderX custom development bases use ADB reverse on device loopback.
  const hosts = options.mode === 'development' ? ['127.0.0.1', 'localhost'] : []
  if (url.protocol === 'http:') hosts.push(url.hostname)
  const domains = [...new Set(hosts)].map(host =>
    `        <domain includeSubdomains="false">${host}</domain>`).join('\n')
  return `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="false" />${domains ? `
    <domain-config cleartextTrafficPermitted="true">
${domains}
    </domain-config>` : ''}
</network-security-config>
`
}

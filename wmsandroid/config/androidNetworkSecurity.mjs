export function renderAndroidNetworkSecurityConfig(apiBaseUrl) {
  const url = new URL(String(apiBaseUrl || '').trim())
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('VITE_API_BASE_URL 必须是 http 或 https 地址')
  }
  const host = url.hostname
  if (!host || !/^[a-z0-9.:-]+$/i.test(host)) {
    throw new Error('VITE_API_BASE_URL 主机名格式无效')
  }
  // HBuilderX custom bases always use an HTTP ADB reverse connection on the
  // device loopback interface. Keeping this narrowly scoped loopback exception
  // in every package avoids relying on HBuilderX's inconsistent Vite mode when
  // it creates a native base, without permitting HTTP to any external host.
  const cleartextHosts = new Set(['127.0.0.1', 'localhost'])
  if (url.protocol === 'http:') cleartextHosts.add(host)
  const domains = [...cleartextHosts]
    .map((domain) => `        <domain includeSubdomains="false">${domain}</domain>`)
    .join('\n')
  const cleartextRule = domains
    ? `\n    <domain-config cleartextTrafficPermitted="true">\n${domains}\n    </domain-config>`
    : ''
  return `<?xml version="1.0" encoding="utf-8"?>\n<network-security-config>\n    <base-config cleartextTrafficPermitted="false" />${cleartextRule}\n</network-security-config>\n`
}

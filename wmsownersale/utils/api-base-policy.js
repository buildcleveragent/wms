// Shared by this client's Vite configuration and runtime; no Node-only imports.
export function validateOwnerApiBase({ value, platform = 'h5', appPlatform = 'android', mode = 'production', allowPrivate = false, allowHttp = false }) {
  const raw = String(value || '').trim().replace(/\/$/, '')
  if (!raw) {
    if (platform !== 'h5') throw new Error('VITE_API_BASE_URL is required for App and mini-program builds.')
    return ''
  }
  // App logic runs outside the browser; do not depend on a global WHATWG URL.
  const match = /^(https?):\/\/(\[[a-f\d:]+\]|[a-z\d.-]+)(?::([1-9]\d{0,4}))?$/i.exec(raw)
  if (!match || (match[3] && Number(match[3]) > 65535)) {
    throw new Error('VITE_API_BASE_URL must be an origin without credentials, path, query or fragment (do not append /api).')
  }
  const protocol = match[1].toLowerCase()
  const hostname = match[2].toLowerCase()
  const host = hostname.replace(/^\[|\]$/g, '')
  const validIpv6 = host.includes(':') && !host.includes(':::') && host.split('::').length <= 2 &&
    host.split(':').filter(Boolean).every(part => /^[a-f\d]{1,4}$/.test(part)) &&
    (host.includes('::') ? host.split(':').filter(Boolean).length < 8 : host.split(':').length === 8)
  const validDns = !host.includes(':') && host.length <= 253 && host.split('.').every(label =>
    /^[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?$/.test(label))
  if (!validIpv6 && !validDns) throw new Error('VITE_API_BASE_URL has an invalid host.')
  const loopback = ['localhost', '127.0.0.1', '::1'].includes(host)
  const parts = host.split('.').map(Number)
  const ipv4 = parts.length === 4 && parts.every(n => Number.isInteger(n) && n >= 0 && n <= 255)
  if (/^[\d.]+$/.test(host) && (!ipv4 || parts.join('.') !== host) || /^0x[\da-f]+$/.test(host)) {
    throw new Error('VITE_API_BASE_URL requires canonical IPv4 notation.')
  }
  const privateHost = loopback || (ipv4 && (parts[0] === 10 || parts[0] === 127 ||
    (parts[0] === 169 && parts[1] === 254) || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) ||
    (parts[0] === 192 && parts[1] === 168))) || (host.includes(':') && /^(fc|fd|fe[89ab])/.test(host))
  const androidHttp = ['app', 'app-plus'].includes(platform) && appPlatform === 'android' && allowHttp
  const devHttp = mode === 'development' && loopback
  if (protocol !== 'https' && !(protocol === 'http' && (androidHttp || devHttp))) {
    throw new Error('VITE_API_BASE_URL requires HTTPS; Android HTTP requires VITE_ALLOW_HTTP_API=true.')
  }
  if (privateHost && !loopback && !allowPrivate) throw new Error('Private API hosts require VITE_ALLOW_PRIVATE_API=true.')
  const port = match[3] && match[3] !== (protocol === 'https' ? '443' : '80') ? `:${match[3]}` : ''
  return `${protocol}://${hostname}${port}`
}

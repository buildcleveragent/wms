const configured = String(import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/$/, '')

function classifyHost(hostname) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '')
  const loopback = ['localhost', '127.0.0.1', '::1'].includes(host)
  const octets = host.split('.').map(Number)
  const ipv4 = octets.length === 4 && octets.every((part) => Number.isInteger(part) && part >= 0 && part <= 255)
  const privateHost =
    loopback ||
    (ipv4 &&
      (octets[0] === 10 ||
        (octets[0] === 169 && octets[1] === 254) ||
        (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
        (octets[0] === 192 && octets[1] === 168))) ||
    host.startsWith('fc') ||
    host.startsWith('fd')
  return { loopback, privateHost }
}

function validate(value) {
  if (!value) return ''
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    throw new Error('VITE_API_BASE_URL must be an absolute URL.')
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error('VITE_API_BASE_URL contains forbidden URL components.')
  }
  const { loopback, privateHost } = classifyHost(parsed.hostname)
  if (parsed.protocol !== 'https:' && !(import.meta.env.DEV && loopback && parsed.protocol === 'http:')) {
    throw new Error('VITE_API_BASE_URL must use HTTPS outside loopback development.')
  }
  if (privateHost && !loopback && import.meta.env.VITE_ALLOW_PRIVATE_API !== 'true') {
    throw new Error('Private API hosts require VITE_ALLOW_PRIVATE_API=true.')
  }
  return value
}

let resolved = validate(configured)
const platform = String(import.meta.env.UNI_PLATFORM || 'h5').toLowerCase()
if (!resolved && platform !== 'h5') {
  throw new Error('VITE_API_BASE_URL is required for App and mini-program builds.')
}

export const BASE_URL = resolved

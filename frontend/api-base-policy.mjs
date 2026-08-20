import net from 'node:net'
import fs from 'node:fs'
import path from 'node:path'

function privateHost(hostname) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '')
  if (host === 'localhost') return true
  if (net.isIP(host) === 4) {
    const parts = host.split('.').map(Number)
    return (
      parts[0] === 10 ||
      parts[0] === 127 ||
      (parts[0] === 169 && parts[1] === 254) ||
      (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) ||
      (parts[0] === 192 && parts[1] === 168)
    )
  }
  return host === '::1' || host.startsWith('fc') || host.startsWith('fd')
}

export function validateApiBase({ value, platform, mode, allowPrivate = false }) {
  const raw = String(value || '').trim().replace(/\/$/, '')
  if (!raw) {
    if (platform !== 'h5') {
      throw new Error('VITE_API_BASE_URL is required for App and mini-program builds.')
    }
    return ''
  }

  let parsed
  try {
    parsed = new URL(raw)
  } catch {
    throw new Error('VITE_API_BASE_URL must be an absolute URL.')
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error('VITE_API_BASE_URL cannot contain credentials, query, or fragment.')
  }
  const development = mode === 'development'
  const normalizedHost = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, '')
  const loopback = ['localhost', '127.0.0.1', '::1'].includes(normalizedHost)
  if (parsed.protocol !== 'https:' && !(development && loopback && parsed.protocol === 'http:')) {
    throw new Error('VITE_API_BASE_URL must use HTTPS outside loopback development.')
  }
  if (privateHost(parsed.hostname) && !loopback && !allowPrivate) {
    throw new Error('Private API hosts require VITE_ALLOW_PRIVATE_API=true.')
  }
  return raw
}

export function validateWeixinManifest({ platform, root = process.cwd() }) {
  if (platform !== 'mp-weixin') return
  const manifestPath = path.join(root, 'manifest.json')
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
  const config = manifest['mp-weixin'] || {}
  if (!/^wx[0-9a-f]{16}$/i.test(String(config.appid || '').trim())) {
    throw new Error('mp-weixin.appid must contain the approved production WeChat AppID.')
  }
  if (config.setting?.urlCheck !== true) {
    throw new Error('mp-weixin.setting.urlCheck must remain enabled for release builds.')
  }
}

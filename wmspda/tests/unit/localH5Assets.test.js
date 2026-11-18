import { describe, expect, it } from 'vitest'
import postcss from 'postcss'
import localH5Assets, { localizeShadowImages } from '../../../frontend/local-h5-assets.mjs'

describe('local uni-app decoration', () => {
  it.each(['grey', 'blue', 'green', 'orange', 'red', 'yellow'])('localizes %s without a network URL', color => {
    for (const host of ['cdn.dcloud.net.cn', 'cdn1000.dcloud.net.cn', 'cdn.dcimg.net']) {
      expect(localizeShadowImages(`url('https://${host}/img/shadow-${color}.png')`))
        .toMatch(/^linear-gradient\(to bottom, rgba\(.+\), rgba\(.+\)\)$/)
    }
  })

  it('handles framework page headers and production preload keyframes', async () => {
    const result = await postcss([localH5Assets()]).process(`
      .uni-page-head-shadow-grey::after { background-image: url("https://cdn.dcloud.net.cn/img/shadow-grey.png") }
      @keyframes shadow-preload { 0%, 100% { background-image: url(https://cdn.dcloud.net.cn/img/shadow-grey.png) } }
    `, { from: undefined })
    expect(result.css).not.toContain('https://')
    expect(result.css.match(/linear-gradient/g)).toHaveLength(2)
  })

  it('preserves business images, local assets and unknown framework assets', () => {
    for (const value of [
      'url(https://example.com/img/shadow-grey.png)',
      'url(/static/shadow-grey.png)',
      'url(https://cdn.dcloud.net.cn/img/logo.png)',
      'url(https://cdn.dcloud.net.cn/img/shadow-unknown.png)',
    ]) expect(localizeShadowImages(value)).toBe(value)
  })
})

// uni-app injects these decorative images, including an off-screen preload.
// Keep their replacements in application CSS instead of contacting a public CDN.
const shadowColors = {
  grey: '0, 0, 0', blue: '0, 122, 255', green: '76, 217, 100',
  orange: '255, 149, 0', red: '255, 59, 48', yellow: '255, 204, 0',
}
const shadowUrl = /url\(\s*(['"]?)https:\/\/(?:cdn\d*\.dcloud\.net\.cn|cdn\.dcimg\.net)\/img\/shadow-(grey|blue|green|orange|red|yellow)\.png\1\s*\)/g

export function localizeShadowImages(value) {
  return value.replace(shadowUrl, (_, quote, color) =>
    `linear-gradient(to bottom, rgba(${shadowColors[color]}, 0.12), rgba(${shadowColors[color]}, 0))`)
}

export default function localH5Assets() {
  return {
    postcssPlugin: 'wms-local-h5-assets',
    Declaration(declaration) {
      if (declaration.value.includes('/img/shadow-')) {
        declaration.value = localizeShadowImages(declaration.value)
      }
    },
  }
}

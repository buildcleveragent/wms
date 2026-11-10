import fs from 'node:fs'
import { expect, it } from 'vitest'
import {
  ANDROID_PACKAGE_NAME,
  DCLOUD_APP_ID,
  assertManifestUpgrade,
  nextPatchVersion,
  normalizeCertificateSha256,
  resolveUpgradeIdentity,
} from '../config/androidReleaseIdentity.mjs'

function pngDimensions(filename) {
  const png = fs.readFileSync(filename)
  expect(png.subarray(1, 4).toString()).toBe('PNG')
  return [png.readUInt32BE(16), png.readUInt32BE(20)]
}

it('keeps the existing Android install identity and native HTTP policy', () => {
  const manifest = JSON.parse(fs.readFileSync('manifest.json', 'utf8'))
  const android = manifest['app-plus'].distribute.android
  expect(manifest.name).toBe('仓储经营分析中心')
  expect(manifest.appid).toBe(DCLOUD_APP_ID)
  expect(manifest['app-plus'].orientation).toEqual(['portrait-primary'])
  expect(android.packagename).toBe(ANDROID_PACKAGE_NAME)
  expect(android.minSdkVersion).toBe(24)
  expect(android.networkSecurityConfig).toBe('xml/network_security_config.xml')
  expect(android.permissions).toEqual(expect.arrayContaining([
    '<uses-permission android:name="android.permission.INTERNET"/>',
    '<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/>',
  ]))
  const nativeManifest = fs.readFileSync('AndroidManifest.xml', 'utf8')
  expect(nativeManifest).toContain(`package="${ANDROID_PACKAGE_NAME}"`)
  expect(nativeManifest).toContain('android:networkSecurityConfig="@xml/network_security_config"')
})

it('provides every DCloud Android icon at the declared dimensions', () => {
  const manifest = JSON.parse(fs.readFileSync('manifest.json', 'utf8'))
  const icons = manifest['app-plus'].distribute.icons.android
  const expected = { hdpi: 72, xhdpi: 96, xxhdpi: 144, xxxhdpi: 192 }
  for (const [density, size] of Object.entries(expected)) {
    expect(icons[density]).toBe(`static/app-icons/icon-${size}.png`)
    expect(pngDimensions(icons[density])).toEqual([size, size])
  }
})

it('derives exactly one upgrade version from verified old-app metadata', () => {
  const fingerprint = [
    '01:23:45:67:89:AB:CD:EF', '01:23:45:67:89:AB:CD:EF',
    '01:23:45:67:89:AB:CD:EF', '01:23:45:67:89:AB:CD:EF',
  ].join(':')
  const identity = resolveUpgradeIdentity({
    packageName: ANDROID_PACKAGE_NAME,
    appId: DCLOUD_APP_ID,
    previousVersionName: '2.7.9',
    previousVersionCode: 431,
    previousSigningCertificateSha256: fingerprint,
  })
  expect(identity).toMatchObject({ versionName: '2.7.10', versionCode: '432' })
  expect(normalizeCertificateSha256(fingerprint)).toBe(fingerprint)
  expect(nextPatchVersion('1.0.99')).toBe('1.0.100')
  expect(() => resolveUpgradeIdentity({
    ...identity, previousVersionCode: 0,
  })).toThrow(/positive integer/)
  expect(() => resolveUpgradeIdentity({
    ...identity, packageName: 'example.wrong',
  })).toThrow(/packageName/)
  expect(() => normalizeCertificateSha256('incomplete')).toThrow(/fingerprint/)

  expect(() => assertManifestUpgrade({ appid: DCLOUD_APP_ID }, identity)).toThrow(/package name/)
  expect(() => assertManifestUpgrade({
    appid: DCLOUD_APP_ID,
    versionName: identity.versionName,
    versionCode: identity.versionCode,
    'app-plus': { distribute: { android: { packagename: ANDROID_PACKAGE_NAME } } },
  }, identity)).not.toThrow()
})

it('supports covering bv2new2.apk version 100 with version 101', () => {
  const identity = resolveUpgradeIdentity({
    packageName: 'uni.app.UNIWMSJYFX',
    appId: DCLOUD_APP_ID,
    previousVersionName: '1.0.0',
    previousVersionCode: 100,
    previousSigningCertificateSha256: 'AB'.repeat(32),
  })
  expect(identity).toMatchObject({ versionName: '1.0.1', versionCode: '101' })
  const manifest = JSON.parse(fs.readFileSync('manifest.json', 'utf8'))
  expect(() => assertManifestUpgrade(manifest, identity)).not.toThrow()
  expect(() => assertManifestUpgrade({ ...manifest, versionCode: '100' }, identity))
    .toThrow(/versionCode/)
})

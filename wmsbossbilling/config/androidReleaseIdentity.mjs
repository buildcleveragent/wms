export const ANDROID_PACKAGE_NAME = 'uni.app.UNIWMSJYFX'
export const DCLOUD_APP_ID = '__UNI__4BA38C0'

export function nextPatchVersion(versionName) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(String(versionName || '').trim())
  if (!match) throw new Error('previousVersionName must use numeric major.minor.patch format')
  return `${match[1]}.${match[2]}.${Number(match[3]) + 1}`
}

export function normalizeCertificateSha256(value) {
  const normalized = String(value || '').replace(/[^a-fA-F0-9]/g, '').toUpperCase()
  if (!/^[A-F0-9]{64}$/.test(normalized)) {
    throw new Error('previousSigningCertificateSha256 must be a complete SHA-256 fingerprint')
  }
  return normalized.match(/.{2}/g).join(':')
}

export function resolveUpgradeIdentity(input = {}) {
  if (input.packageName !== ANDROID_PACKAGE_NAME) {
    throw new Error(`packageName must remain ${ANDROID_PACKAGE_NAME}`)
  }
  if (input.appId !== DCLOUD_APP_ID) throw new Error(`appId must remain ${DCLOUD_APP_ID}`)
  const previousVersionCode = Number(input.previousVersionCode)
  if (!Number.isSafeInteger(previousVersionCode) || previousVersionCode < 1) {
    throw new Error('previousVersionCode must be the real APK value and a positive integer')
  }
  return {
    packageName: ANDROID_PACKAGE_NAME,
    appId: DCLOUD_APP_ID,
    previousVersionName: String(input.previousVersionName),
    previousVersionCode,
    previousSigningCertificateSha256: normalizeCertificateSha256(
      input.previousSigningCertificateSha256,
    ),
    versionName: nextPatchVersion(input.previousVersionName),
    versionCode: String(previousVersionCode + 1),
  }
}

export function assertManifestUpgrade(manifest, identity) {
  if (manifest.appid !== identity.appId) {
    throw new Error('manifest AppID does not match the old app')
  }
  const android = manifest['app-plus']?.distribute?.android
  if (android?.packagename !== identity.packageName) {
    throw new Error('manifest package name does not match the old app')
  }
  if (manifest.versionName !== identity.versionName) {
    throw new Error(`manifest versionName must be ${identity.versionName}`)
  }
  if (String(manifest.versionCode) !== identity.versionCode) {
    throw new Error(`manifest versionCode must be ${identity.versionCode}`)
  }
}

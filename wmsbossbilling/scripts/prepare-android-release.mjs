import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  assertManifestUpgrade,
  resolveUpgradeIdentity,
} from '../config/androidReleaseIdentity.mjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const identityPath = process.env.WMS_BOSS_RELEASE_IDENTITY_FILE
  ? path.resolve(process.env.WMS_BOSS_RELEASE_IDENTITY_FILE)
  : path.join(root, 'config/android/release-identity.local.json')
const manifestPath = path.join(root, 'manifest.json')

if (!fs.existsSync(identityPath)) {
  throw new Error(
    `Missing ${identityPath}. Copy release-identity.example.json, `
      + 'then enter values read from the old APK.',
  )
}

const identity = resolveUpgradeIdentity(JSON.parse(fs.readFileSync(identityPath, 'utf8')))
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
const mode = process.argv[2]

if (mode === '--apply') {
  manifest.versionName = identity.versionName
  manifest.versionCode = identity.versionCode
  assertManifestUpgrade(manifest, identity)
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)
  console.log(`Prepared Android upgrade ${identity.versionName} (${identity.versionCode}).`)
  console.log(`Expected old signing certificate: ${identity.previousSigningCertificateSha256}`)
} else if (mode === '--check') {
  assertManifestUpgrade(manifest, identity)
  console.log(
    `Android upgrade identity verified for ${identity.versionName} (${identity.versionCode}).`,
  )
  console.log(`Expected old signing certificate: ${identity.previousSigningCertificateSha256}`)
} else {
  throw new Error('Use --apply to update manifest.json or --check to verify it.')
}

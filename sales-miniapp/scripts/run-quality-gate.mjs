import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const appRoot = path.resolve(scriptDir, '..')
const repoRoot = path.resolve(appRoot, '..')
const args = new Set(process.argv.slice(2))

const skipBuild = args.has('--skip-build')
const runDb = args.has('--db')
const runDataAccuracy = args.has('--data-accuracy') || runDb
const fastDb = args.has('--fast-db') || args.has('--no-migrations')
const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm'

if (runDb && skipBuild) {
  throw new Error('release mode forbids --skip-build')
}
if (runDb && fastDb) {
  throw new Error('release mode forbids --fast-db and --no-migrations')
}

function safeTestDatabaseName(value) {
  const name = String(value || '').trim()
  if (name !== 'wms_db_test') {
    throw new Error('DB_TEST_NAME must be exactly wms_db_test')
  }
  return name
}

function commandExists(command) {
  if (path.isAbsolute(command) || command.includes(path.sep)) {
    return fs.existsSync(command)
  }
  return true
}

function firstExisting(candidates) {
  return candidates.find(commandExists) || candidates[candidates.length - 1]
}

const pythonCommand = process.env.PYTHON || firstExisting([
  path.join(repoRoot, '.venv', 'bin', 'python'),
  path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
  'python3',
  'python',
])

const testDatabaseName = safeTestDatabaseName(process.env.DB_TEST_NAME || 'wms_db_test')
const backendEnv = {
  ...process.env,
  APP_ENV: 'test',
  SECRET_KEY:
    process.env.SECRET_KEY ||
    'sale-mini-test-key-0123456789abcdef0123456789abcdef0123456789abcdef',
  DB_TEST_NAME: testDatabaseName,
  ...(runDb
    ? { DB_NAME: testDatabaseName }
    : {}),
  CORS_ALLOWED_ORIGINS:
    process.env.CORS_ALLOWED_ORIGINS || 'http://localhost,http://127.0.0.1',
}

if (runDb) {
  console.log(`- TEST DATABASE ${testDatabaseName}`)
}

const steps = []

function addStep(name, command, commandArgs, options = {}) {
  steps.push({
    name,
    command,
    args: commandArgs,
    cwd: options.cwd || process.cwd(),
    env: options.env || process.env,
    skip: Boolean(options.skip),
  })
}

addStep('frontend mall structure', 'node', ['scripts/verify-mall-structure.mjs'], {
  cwd: appRoot,
})
addStep('frontend payment behavior', 'node', ['--test', 'tests/payment-service.test.mjs'], {
  cwd: appRoot,
})

addStep('frontend authentication recovery', 'node', ['--test', 'tests/request-auth.test.mjs'], { cwd: appRoot })
addStep('cart uncertainty recovery', 'node', ['--test', 'tests/cart-recovery.test.mjs'], { cwd: appRoot })

if (fs.existsSync(path.join(repoRoot, 'manage.py'))) {
  addStep('django system check', pythonCommand, ['manage.py', 'check'], {
    cwd: repoRoot,
    env: backendEnv,
  })
  addStep(
    'sale-mini pure unit tests',
    pythonCommand,
    [
      '-m',
      'pytest',
      '-q',
      'allapp/salesapp/test_salemini_unit.py',
      'allapp/salesapp/test_services_pricing_unit.py',
    ],
    {
      cwd: repoRoot,
      env: backendEnv,
    },
  )
  addStep(
    'sale-mini api db tests',
    pythonCommand,
    [
      '-m',
      'pytest',
      '-q',
      '--reuse-db',
      '--disable-warnings',
      'allapp/salesapp/tests.py::SaleMiniApiTests',
      'allapp/salesapp/tests.py::SaleMiniPaymentConcurrencyTests',
    ],
    {
      cwd: repoRoot,
      env: backendEnv,
      skip: !runDb,
    },
  )
  addStep(
    'sale-mini console catalog db tests',
    pythonCommand,
    [
      '-m',
      'pytest',
      '-q',
      '--reuse-db',
      '--disable-warnings',
      'allapp/console/tests.py',
    ],
    {
      cwd: repoRoot,
      env: backendEnv,
      skip: !runDb,
    },
  )
  addStep(
    'sale-mini admin owner bulk listing db tests',
    pythonCommand,
    [
      '-m',
      'pytest',
      '-q',
      '--reuse-db',
      '--disable-warnings',
      'allapp/salesapp/test_admin_bulk_listing.py',
    ],
    {
      cwd: repoRoot,
      env: backendEnv,
      skip: !runDb,
    },
  )
  addStep(
    'sale-mini data accuracy validation',
    pythonCommand,
    [
      'manage.py',
      'validate_sale_mini_data_accuracy',
      '--fail-on-issues',
      '--limit',
      '20',
    ],
    {
      cwd: repoRoot,
      env: backendEnv,
      skip: !runDataAccuracy,
    },
  )
} else {
  console.log('backend checks skipped: manage.py was not found next to sales-miniapp')
}

addStep('wechat miniapp build', npmCommand, ['run', 'build:mp-weixin'], {
  cwd: appRoot,
  skip: skipBuild || !runDb,
})
addStep('h5 build', npmCommand, ['run', 'build:h5'], {
  cwd: appRoot,
  skip: skipBuild,
})

let failed = false

for (const step of steps) {
  if (step.skip) {
    console.log(`- SKIP ${step.name}`)
    continue
  }
  console.log(`- RUN ${step.name}`)
  const result = spawnSync(step.command, step.args, {
    cwd: step.cwd,
    env: step.env,
    stdio: 'inherit',
  })
  if (result.error) {
    console.error(`- FAIL ${step.name}: ${result.error.message}`)
    failed = true
    break
  }
  if (result.status !== 0) {
    console.error(`- FAIL ${step.name}: exit ${result.status}`)
    failed = true
    break
  }
  console.log(`- PASS ${step.name}`)
}

if (failed) {
  process.exit(1)
}

console.log('sales-miniapp quality gate passed')

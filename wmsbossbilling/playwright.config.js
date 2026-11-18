import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  retries: 0,
  failOnFlakyTests: true,
  outputDir: process.env.WMS_BROWSER_ARTIFACTS || 'test-results',
  reporter: [['list'], ['json', { outputFile: `${process.env.WMS_BROWSER_ARTIFACTS || 'test-results'}/results.json` }]],
  timeout: 60000,
  use: {
    navigationTimeout: 30000,
    baseURL: 'http://127.0.0.1:5176',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: process.env.BOSS_E2E_BUILT === '1'
      ? 'CI=1 UNI_INPUT_DIR=$PWD vite preview --outDir dist/build/h5 --host 127.0.0.1 --port 5176 --strictPort'
      : 'npm run dev:h5',
    url: 'http://127.0.0.1:5176',
    reuseExistingServer: false,
    timeout: 120000,
  },
})

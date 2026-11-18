import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  retries: 0,
  failOnFlakyTests: true,
  timeout: 90000,
  expect: { timeout: 15000 },
  outputDir: process.env.WMS_BROWSER_ARTIFACTS || 'test-results',
  reporter: [['list'], ['json', { outputFile: `${process.env.WMS_BROWSER_ARTIFACTS || 'test-results'}/results.json` }]],
  use: {
    baseURL: 'http://127.0.0.1:5177',
    navigationTimeout: 30000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'CI=1 UNI_INPUT_DIR=$PWD vite preview --outDir dist/build/h5 --host 127.0.0.1 --port 5177 --strictPort',
    url: 'http://127.0.0.1:5177',
    reuseExistingServer: false,
    timeout: 120000,
  },
})

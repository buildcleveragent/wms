import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 90_000,
  workers: 1,
  retries: 0,
  failOnFlakyTests: true,
  outputDir: process.env.WMS_BROWSER_ARTIFACTS || 'test-results',
  reporter: [['list'], ['json', { outputFile: `${process.env.WMS_BROWSER_ARTIFACTS || 'test-results'}/results.json` }]],
  use: {
    navigationTimeout: 30000,
    baseURL: 'http://127.0.0.1:5173',
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: process.env.PDA_E2E_BUILT === '1'
      ? 'CI=1 UNI_INPUT_DIR=$PWD vite preview --outDir dist/build/h5 --host 127.0.0.1 --port 5173 --strictPort'
      : 'npm run dev:h5',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: false,
    timeout: 120_000,
  },
})

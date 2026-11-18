import { test as base, expect } from '@playwright/test'
import { instrumentBrowserTests } from '../../frontend/browser-diagnostics.mjs'
export const test = instrumentBrowserTests(base, expect)
export { expect }

import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue({ template: { compilerOptions: {
    isCustomElement: tag => ['view', 'text', 'scroll-view', 'picker'].includes(tag),
  } } })],
  resolve: { alias: { '@': fileURLToPath(new URL('.', import.meta.url)) } },
  test: {
    environment: 'jsdom',
    include: ['tests/unit/**/*.test.js'],
    clearMocks: true,
    restoreMocks: true,
  },
})

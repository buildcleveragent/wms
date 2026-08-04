import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue({
    template: {
      compilerOptions: {
        isCustomElement: (tag) => [
          'view', 'text', 'scroll-view', 'radio-group', 'radio', 'image',
        ].includes(tag),
      },
    },
  })],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('.', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.js'],
    clearMocks: true,
    restoreMocks: true,
  },
})

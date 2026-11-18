// Shared test instrumentation; never imports a client's Playwright installation.
const sessions = new WeakMap()
const shadowHost = /https:\/\/(?:cdn\d*\.dcloud\.net\.cn|cdn\.dcimg\.net)\/img\/shadow-/

function safeUrl(value) {
  try {
    const url = new URL(value)
    return `${url.origin}${url.pathname}`
  } catch { return '' }
}

function safeText(value) {
  return String(value).replace(/https?:\/\/[^\s)"']+/g, safeUrl)
    .replace(/Bearer\s+\S+/gi, 'Bearer [redacted]')
}

export function expectHttpFailure(page, path, status) {
  const session = sessions.get(page)
  if (!session) throw new Error('Use the shared browser test fixture before declaring an HTTP failure')
  session.expected.add(`${path}:${status}`)
}

export function instrumentBrowserTests(base, expect) {
  return base.extend({
    browserDiagnostics: [async ({ page, baseURL }, use, testInfo) => {
      const state = { expected: new Set(), events: [], shadows: [] }
      sessions.set(page, state)
      const localOrigin = new URL(baseURL).origin
      // APIs are explicitly mocked by each test. All other off-origin requests
      // fail immediately, proving that basic UI does not require public CDNs.
      await page.route('**/*', route => {
        const url = new URL(route.request().url())
        if (url.origin === localOrigin || url.pathname.startsWith('/api/')) return route.continue()
        return route.abort('internetdisconnected')
      })
      page.on('request', request => {
        if (shadowHost.test(request.url())) state.shadows.push(safeUrl(request.url()))
      })
      page.on('requestfailed', request => state.events.push({
        kind: 'resource', url: safeUrl(request.url()), detail: safeText(request.failure()?.errorText),
      }))
      page.on('response', response => {
        if (response.status() >= 400) state.events.push({
          kind: 'http', url: safeUrl(response.url()), status: response.status(),
        })
      })
      page.on('pageerror', error => state.events.push({ kind: 'application', detail: safeText(error.message) }))
      page.on('console', message => {
        if (['error', 'warning'].includes(message.type())) state.events.push({
          kind: 'console', level: message.type(), url: safeUrl(message.location().url), detail: safeText(message.text()),
        })
      })
      await use()
      const expected = event => {
        if (!event.url) return false
        const path = new URL(event.url).pathname
        if (event.kind === 'http') return state.expected.has(`${path}:${event.status}`)
        // Only a browser's HTTP resource error for the declared endpoint/status
        // is expected; application exceptions containing "503" still fail.
        if (event.kind === 'console' && /^Failed to load resource:/.test(event.detail)) {
          const status = event.detail.match(/\b([45]\d\d)\b/)?.[1]
          return status && state.expected.has(`${path}:${status}`)
        }
        return false
      }
      const failures = state.events.filter(event =>
        !(event.kind === 'console' && event.level === 'warning') && !expected(event))
      const report = {
        status: testInfo.status, retry: testInfo.retry,
        timeout: testInfo.errors.some(error => /timeout/i.test(error.message || '')),
        expectedHttpErrors: [...state.expected], events: state.events,
        unexpected: failures, externalShadows: state.shadows,
      }
      await testInfo.attach('browser-diagnostics', {
        body: Buffer.from(JSON.stringify(report, null, 2)), contentType: 'application/json',
      })
      if (failures.length || state.shadows.length || testInfo.status !== testInfo.expectedStatus) {
        await page.screenshot({ path: testInfo.outputPath('diagnostic.png') }).catch(() => {})
      }
      sessions.delete(page)
      expect(state.shadows, 'Framework decoration must be local').toEqual([])
      expect(failures, 'Unexpected application/resource errors (see browser-diagnostics)').toEqual([])
    }, { auto: true }],
  })
}

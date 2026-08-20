import { describe, expect, it } from 'vitest'

import { classifyError } from '../utils/request'

describe('boss request error classification', () => {
  it.each([
    [0, 'NETWORK_ERROR'],
    [401, 'UNAUTHENTICATED'],
    [403, 'FORBIDDEN'],
    [500, 'SERVER_ERROR'],
    [422, 'BUSINESS_ERROR'],
  ])('classifies %s as %s', (status, kind) => {
    expect(classifyError(status, {}).kind).toBe(kind)
  })
})

import { describe, expect, it } from 'vitest'

import { mergeUniqueById } from '@/utils/pagination'

describe('pagination merge', () => {
  it('is deterministic across repeated page merges', () => {
    for (let run = 0; run < 20; run += 1) {
      expect(
        mergeUniqueById(
          [{ id: 1, name: 'old' }, { id: 2 }],
          [{ id: 1, name: 'new' }, { id: 3 }],
        ),
      ).toEqual([{ id: 1, name: 'new' }, { id: 2 }, { id: 3 }])
    }
  })

  it('can preserve the first page value while de-duplicating', () => {
    expect(
      mergeUniqueById([{ id: 1, value: 'first' }], [{ id: 1, value: 'second' }], {
        replace: false,
      }),
    ).toEqual([{ id: 1, value: 'first' }])
  })
})

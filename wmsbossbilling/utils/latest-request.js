// A response can commit only while both its request and input context are current.
export function createLatestRequest(readKey = () => '') {
  let revision = 0
  let disposed = false
  return {
    begin() {
      const id = ++revision
      const key = readKey()
      return () => !disposed && revision === id && readKey() === key
    },
    invalidate() { revision++ },
    dispose() { disposed = true; revision++ },
  }
}


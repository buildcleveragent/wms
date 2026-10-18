import { onUnload } from '@dcloudio/uni-app'
import { createLatestRequest } from './latest-request'

export function useLatestRequest(readKey) {
  const requests = createLatestRequest(readKey)
  onUnload(() => requests.dispose())
  return requests
}

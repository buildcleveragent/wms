import { onUnmounted, ref } from 'vue'
import { onHide, onShow, onUnload } from '@dcloudio/uni-app'

export function useBarcodeScanner({ onScan } = {}) {
  const lastScan = ref('')
  const canScan = ref(false)
  let urovo = null
  let mainActivity = null
  let receiver = null
  let receiverRegistered = false
  let active = false
  let disposed = false
  let onScanCallback = typeof onScan === 'function' ? onScan : null

  const quickScan = () => {
    try {
      // #ifdef APP-PLUS
      if (active && urovo && urovo.startScan) {
        urovo.startScan()
      } else {
        uni.showToast({ title: '当前环境不支持扫描', icon: 'none' })
      }
      // #endif
    } catch (error) {
      uni.showToast({ title: '无法触发扫描', icon: 'none' })
    }
  }

  const registerBroadcast = () => {
    if (disposed || !active || receiverRegistered || !mainActivity) return
    try {
      // #ifdef APP-PLUS
      const plusObj = typeof plus !== 'undefined' ? plus : null
      if (!plusObj?.android) return
      const IntentFilter = plusObj.android.importClass('android.content.IntentFilter')
      const filter = new IntentFilter()
      filter.addAction('android.intent.ACTION_DECODE_DATA')
      const localReceiver = plusObj.android.implements(
        'io.dcloud.feature.internal.reflect.BroadcastReceiver',
        {
          onReceive(context, intent) {
            if (!active || disposed || localReceiver !== receiver) return
            plusObj.android.importClass(intent)
            const code = intent.getStringExtra('barcode_string')
            if (!code) return
            lastScan.value = code
            if (uni.vibrateShort) uni.vibrateShort()
            if (onScanCallback) onScanCallback(code)
          },
        },
      )
      receiver = localReceiver
      mainActivity.registerReceiver(localReceiver, filter)
      receiverRegistered = true
      // #endif
    } catch (error) {
      receiver = null
      receiverRegistered = false
      console.error('注册广播失败:', error)
    }
  }

  const unRegisterBroadcast = ({ dispose = false } = {}) => {
    active = false
    try {
      // #ifdef APP-PLUS
      if (receiverRegistered && mainActivity && receiver) {
        mainActivity.unregisterReceiver(receiver)
      }
      // #endif
    } catch (error) {
      console.error('注销广播失败:', error)
    } finally {
      receiverRegistered = false
      receiver = null
      if (dispose) {
        disposed = true
        onScanCallback = null
        urovo = null
        mainActivity = null
        canScan.value = false
      }
    }
  }

  const initScanner = () => {
    if (disposed) return
    active = true
    try {
      // #ifdef APP-PLUS
      const plusObj = typeof plus !== 'undefined' ? plus : null
      if (!plusObj?.android) {
        canScan.value = false
        return
      }
      urovo ||= uni.requireNativePlugin('TH-PlatformSDK')
      mainActivity ||= plusObj.android.runtimeMainActivity()
      canScan.value = Boolean(urovo)
      registerBroadcast()
      // #endif
    } catch (error) {
      canScan.value = false
      console.error('初始化扫描功能失败:', error)
    }
  }

  const disposeScanner = () => unRegisterBroadcast({ dispose: true })

  onShow(initScanner)
  onHide(unRegisterBroadcast)
  onUnload(disposeScanner)
  onUnmounted(disposeScanner)

  return { lastScan, canScan, quickScan }
}

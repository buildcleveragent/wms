import { ref, onMounted, onUnmounted } from 'vue'

export function useBarcodeScanner() {
  const lastScan = ref("")
  const canScan = ref(false)
  const _urovo = ref(null)
  const _mainActivity = ref(null)
  const _receiver = ref(null)
  const receiverRegistered = ref(false)

  // 扫描结果回调函数
  let onScanCallback = null

  const setScanCallback = (callback) => {
    onScanCallback = callback
  }

  const quickScan = () => {
    try {
      // #ifdef APP-PLUS
      if (_urovo.value && _urovo.value.startScan) {
        _urovo.value.startScan()
      } else {
        uni.showToast({ title: "当前环境不支持扫描", icon: "none" })
      }
      // #endif
    } catch (e) {
      uni.showToast({ title: "无法触发扫描", icon: "none" })
    }
  }

  const registerBroadcast = () => {
    try {
      if (receiverRegistered.value || !_mainActivity.value) return
      // #ifdef APP-PLUS
      const IntentFilter = plus.android.importClass("android.content.IntentFilter")
      const filter = new IntentFilter()
      filter.addAction("android.intent.ACTION_DECODE_DATA")

      const BroadcastReceiver = plus.android.implements(
        "io.dcloud.feature.internal.reflect.BroadcastReceiver",
        {
          onReceive: function(context, intent) {
            plus.android.importClass(intent)
            const code = intent.getStringExtra("barcode_string")
            if (code) {
              lastScan.value = code
              uni.vibrateShort && uni.vibrateShort()
              
              // 如果有回调函数，执行回调
              if (onScanCallback && typeof onScanCallback === 'function') {
                onScanCallback(code)
              }
            }
          }
        }
      )
      _receiver.value = BroadcastReceiver
      _mainActivity.value.registerReceiver(BroadcastReceiver, filter)
      receiverRegistered.value = true
      // #endif
    } catch (e) {
      console.error('注册广播失败:', e)
    }
  }

  const unRegisterBroadcast = () => {
    try {
      if (!receiverRegistered.value || !_mainActivity.value || !_receiver.value) return
      // #ifdef APP-PLUS
      _mainActivity.value.unregisterReceiver(_receiver.value)
      // #endif
      receiverRegistered.value = false
      _receiver.value = null
      onScanCallback = null // 清理回调
    } catch (e) {
      console.error('注销广播失败:', e)
    }
  }

  const initScanner = () => {
    try {
      // #ifdef APP-PLUS
      _urovo.value = uni.requireNativePlugin("TH-PlatformSDK")
      
      const plusObj = typeof plus !== "undefined" ? plus : null
      if (plusObj && plusObj.android) {
        _mainActivity.value = plus.android.runtimeMainActivity()
        canScan.value = !!_urovo.value
        registerBroadcast()
      }
      // #endif
    } catch (e) {
      canScan.value = false
      console.error('初始化扫描功能失败:', e)
    }
  }

  // 生命周期在调用时手动管理
  return {
    lastScan,
    canScan,
    quickScan,
    setScanCallback,
    initScanner,
    unRegisterBroadcast
  }
}
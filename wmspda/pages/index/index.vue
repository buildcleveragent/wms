<template>
  <!-- 模板部分完全兼容，无需修改 -->
  <view class="page">
<!--    <view class="header">
      <text class="title">仓库作业工作台</text>
    </view> -->

    <view class="grid">
      <view class="card" v-for="item in actions" :key="item.key" @click="go(item)">
        <view class="emoji">{{ item.emoji }}</view>
        <text class="card-title">{{ item.title }}</text>
      </view>
    </view>

<!--    <view class="section" v-if="lastScan">
      <text class="section-title">最近一次扫描结果</text>
      <view class="scan-box">
        <text class="scan-text">{{ lastScan }}</text>
      </view>
    </view> -->

<!--    <view class="fab" @click="quickScan" v-if="canScan">
      <text class="fab-text">扫</text>
    </view> -->
  </view>
</template>

<script>
import { ref, onMounted, onUnmounted } from 'vue'

export default {
  name: "WarehouseAdminHome",
  setup() {
    // 响应式数据
    const actions = ref([
      { key: "receiving", title: "收货(有订单)", emoji: "📋", desc: "到货验收/收货登记", op: "receive" },
      { key: "receivewithoutorder", title: "收货(无订单)", emoji: "📥", desc: "到货验收/收货登记", op: "receivewithoutorder",path: "/pages/inbound/createwithoutorder/selectowner" },
      { key: "putaway",   title: "上架", emoji: "📦", desc: "库位分配/上架确认",  op: "putaway" },                                               
      { key: "picking",   title: "拣货", emoji: "🧾", desc: "波次拣货/拣货确认",   op: "pick",path: "/pages/picking/task_list" },
      { key: "recheck",   title: "复核", emoji: "✅", desc: "对拣货结果复核",     op: "recheck", path: "/pages/review/pick_task_list" },
      { key: "pack",      title: "打包", emoji: "🎁", desc: "装箱/封箱/打印",     op: "pack" },
      { key: "shipping",  title: "发运", emoji: "🚚", desc: "复核装车/出库发运",  op: "ship" },
      { key: "replenish", title: "补货", emoji: "🔀", desc: "从存储区到拣货区",   op: "replenish",path: "/pages/inventory/replenish/index" },
      { key: "move",      title: "移库", emoji: "🔁", desc: "库内移位/合并/分拆", op: "move",path: "/pages/inventory/move/index" },
      { key: "stocktake", title: "盘点", emoji: "🧮", desc: "周期盘点/抽盘/全盘", op: "stocktake",path: "/pages/inventory/stocktake/index" },
	  { key: "stocktake", title: "查询", emoji: "🧮", desc: "查询", op: "chaxun",path: "/pages/inventory/company" },
    ])
    
    const lastScan = ref("")
    const canScan = ref(false)
    const _urovo = ref(null)
    const _mainActivity = ref(null)
    const _receiver = ref(null)
    const receiverRegistered = ref(false)

    // 方法
    const go = (item) => {
      console.log("👉 go() 被调用，准备跳转：", item.path)
      uni.showToast({ title: "跳转中...", icon: "none" })
      uni.navigateTo({ url: item.path })
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
      } catch (e) {
        console.error('注销广播失败:', e)
      }
    }

    // 生命周期
    onMounted(() => {
      try {
        // #ifdef APP-PLUS
        // 修复：移除有语法错误的行
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
    })

    onUnmounted(() => {
      unRegisterBroadcast()
    })

    // 返回模板需要的数据和方法
    return {
      actions,
      lastScan,
      canScan,
      go,
      quickScan
    }
  }
}
</script>

<style scoped>
/* 样式部分完全兼容，无需修改 */
.page { padding: 24rpx; }
.header { margin-top: 8rpx; margin-bottom: 20rpx; }
.title { font-size: 40rpx; font-weight: 700; }
.subtitle { margin-top: 8rpx; color: #666; font-size: 26rpx; }
.grid { display: grid; grid-template-columns: repeat(4, 1fr); grid-gap: 24rpx; }
.card { background: #fff; border-radius: 24rpx; padding: 28rpx; box-shadow: 0 6rpx 24rpx rgba(0,0,0,.05); }
.card:active { opacity: .85; }
.emoji { font-size: 56rpx; margin-bottom: 10rpx; }
.card-title { font-size: 32rpx; font-weight: 600; }
.card-desc { margin-top: 6rpx; color: #888; font-size: 24rpx; }
.section { margin-top: 28rpx; }
.section-title { font-size: 28rpx; color: #333; margin-bottom: 12rpx; }
.scan-box { background: #f7f7f9; border-radius: 18rpx; padding: 20rpx; }
.scan-text { font-size: 28rpx; word-break: break-all; }
.fab { position: fixed; right: 36rpx; bottom: 60rpx; width: 100rpx; height: 100rpx; background: #007aff; border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 12rpx 30rpx rgba(0,0,0,.15); }
.fab-text { color: #fff; font-size: 36rpx; font-weight: 700; }
</style>
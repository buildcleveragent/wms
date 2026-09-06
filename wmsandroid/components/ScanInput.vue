<template>
  <view :class="['scan-input', { active, disabled }]">
    <input
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      @input="$emit('update:modelValue', $event.detail.value)"
      @focus="$emit('focus')"
      @confirm="$emit('confirm', modelValue)"
    />
    <button :disabled="disabled || scanning" @click="scan">
      <WmsIcon name="scan" /><text>{{ scanning ? '扫描中' : '扫码' }}</text>
    </button>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import WmsIcon from '@/components/WmsIcon.vue'
import { explainScanError, scanOnce } from '@/utils/cameraScanner'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '扫描或输入条码' },
  disabled: { type: Boolean, default: false },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'scan', 'focus', 'confirm'])
const scanning = ref(false)

async function scan() {
  if (props.disabled || scanning.value) return
  scanning.value = true
  try {
    const result = await scanOnce()
    emit('update:modelValue', result.rawValue)
    emit('scan', result.rawValue, result)
  } catch (error) {
    await explainScanError(error)
  } finally {
    scanning.value = false
  }
}
</script>

<style scoped>
.scan-input { min-height: 86rpx; border: 1rpx solid #cbd5e1; border-radius: 16rpx; background: #fff; display: flex; align-items: center; overflow: hidden; }
.scan-input.active { border: 2rpx solid #1677ff; box-shadow: 0 0 0 5rpx rgba(22,119,255,.08); }.scan-input.disabled{opacity:.5}
.scan-input input { flex: 1; min-width: 0; height: 84rpx; padding: 0 20rpx; font-size: 26rpx; }
.scan-input button { width: 132rpx; height: 72rpx; margin-right: 7rpx; padding: 0; border-radius: 13rpx; background: #1677ff; color: #fff; display: flex; align-items: center; justify-content: center; gap: 8rpx; font-size: 23rpx; }.scan-input button :deep(svg){font-size:28rpx}
</style>

<template>
  <view class="period-filter">
    <view class="presets">
      <button v-for="option in datePresets" :key="option.key" size="mini" :disabled="disabled"
        :class="{ selected: preset === option.key }" @click="select(option.key)">{{ option.label }}</button>
      <button size="mini" :disabled="disabled" :class="{ selected: preset === 'custom' }"
        @click="toggleCustom">自定义</button>
    </view>
    <view v-if="editing" class="custom-range">
      <!-- #ifdef H5 -->
      <label class="date-field">开始日期
        <component :is="'input'" type="date" aria-label="开始日期" min="1000-01-01" :max="today"
          :value="from" @input="from = $event.target.value" />
      </label>
      <label class="date-field">结束日期
        <component :is="'input'" type="date" aria-label="结束日期" :min="from" :max="today"
          :value="to" @input="to = $event.target.value" />
      </label>
      <!-- #endif -->
      <!-- #ifndef H5 -->
      <picker mode="date" start="1000-01-01" :end="today" :value="from" @change="from = $event.detail.value">
        <view class="date-field">开始日期 {{ from }}</view>
      </picker>
      <picker mode="date" :start="from" :end="today" :value="to" @change="to = $event.detail.value">
        <view class="date-field">结束日期 {{ to }}</view>
      </picker>
      <!-- #endif -->
      <button size="mini" :disabled="disabled || !validRange(from, to, today)" @click="apply">应用</button>
      <text v-if="from && to && !validRange(from, to, today)" class="date-error">开始日期不能晚于结束日期，结束日期不能晚于今天</text>
    </view>
  </view>
</template>
<script setup>
import { ref } from 'vue'
import { datePresets, validRange } from '@/utils/date-ranges'
const props = defineProps({ preset: String, range: Object, today: String, disabled: Boolean })
const emit = defineEmits(['select'])
const editing = ref(false), from = ref(''), to = ref('')
function select(key) {
  editing.value = false
  emit('select', { preset: key })
}
function toggleCustom() {
  if (!editing.value) {
    from.value = props.range.date_from
    to.value = props.range.date_to
  }
  editing.value = !editing.value
}
function apply() {
  if (!validRange(from.value, to.value, props.today)) return
  editing.value = false
  emit('select', { preset: 'custom', date_from: from.value, date_to: to.value })
}
</script>
<style scoped>
.period-filter { margin-top: 14rpx; }
.presets, .custom-range { display: flex; flex-wrap: wrap; align-items: center; gap: 8rpx; }
.period-filter button { margin: 0; padding: 0 14rpx; font-size: 24rpx; }
.presets .selected { background: #e4efff; color: #1656a4; }
.custom-range { margin-top: 10rpx; }
.date-field { padding: 10rpx; border: 1px solid #d9e3f2; border-radius: 8rpx; font-size: 24rpx; }
.date-field input { font: inherit; color: inherit; }
.date-error { color: #a85c24; font-size: 23rpx; }
</style>

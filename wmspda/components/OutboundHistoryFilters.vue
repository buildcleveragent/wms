<template>
  <view class="filter-grid">
    <input class="history-input" :value="modelValue.search" placeholder="单号、客户、商品名称／品牌型号／条码" confirm-type="search" @input="set('search', $event.detail.value)" @confirm="$emit('query')" />
    <picker mode="date" :value="modelValue.start_date" @change="set('start_date', $event.detail.value)"><view class="picker-field">开始：{{ modelValue.start_date }}</view></picker>
    <picker mode="date" :value="modelValue.end_date" @change="set('end_date', $event.detail.value)"><view class="picker-field">结束：{{ modelValue.end_date }}</view></picker>
    <picker v-for="field in fields" :key="field.key" :range="choices(field).map(x => x.label)" :value="index(field)" @change="set(field.key, choices(field)[Number($event.detail.value)].value)">
      <view class="picker-field">{{ choices(field)[index(field)].label }}</view>
    </picker>
  </view>
</template>
<script setup>
import { computed } from 'vue'
const props = defineProps({ modelValue: { type: Object, required: true }, options: { type: Object, default: () => ({}) }, assisted: Boolean })
const emit = defineEmits(['update:modelValue', 'query'])
const fields = computed(() => [
  { key: 'owner_id', options: 'owners', label: '全部货主', entity: true },
  ...(props.assisted ? [
    { key: 'operator_id', options: 'operators', label: '全部操作员', entity: true },
    { key: 'status', options: 'statuses', label: '全部作业状态' },
  ] : [
    { key: 'warehouse_id', options: 'warehouses', label: '全部仓库', entity: true },
    { key: 'processing_mode', options: 'processing_modes', label: '全部处理方式' },
    { key: 'outbound_type', options: 'outbound_types', label: '全部出库类型' },
    { key: 'submit_status', options: 'submit_statuses', label: '全部提交状态' },
    { key: 'approval_status', options: 'approval_statuses', label: '全部审核状态' },
    { key: 'is_closed', options: 'closed_statuses', label: '全部关闭状态' },
  ]),
])
function choices(field) { return [{ value: '', label: field.label }, ...(props.options[field.options] || []).map(x => ({ value: field.entity ? x.id : x.value, label: field.entity ? x.name || x.username || x.code : x.label }))] }
function index(field) { return Math.max(0, choices(field).findIndex(x => String(x.value) === String(props.modelValue[field.key]))) }
function set(key, value) { emit('update:modelValue', { ...props.modelValue, [key]: value }) }
</script>
<style scoped>
.filter-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14rpx; }
.history-input, .picker-field { box-sizing: border-box; width: 100%; height: 76rpx; padding: 0 16rpx; border: 1rpx solid #d9dee7; border-radius: 10rpx; background: #fff; font-size: 26rpx; }
.history-input { grid-column: 1 / -1; }
.picker-field { line-height: 76rpx; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 600px) { .filter-grid { grid-template-columns: 1fr 1fr; } }
</style>

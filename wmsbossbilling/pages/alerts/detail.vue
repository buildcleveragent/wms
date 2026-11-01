<template>
  <view class="page">
    <view class="title">预警详情（只读）</view>
    <BossDataStatus :meta="meta" :error="dataError" />
    <view v-if="loading" class="card">加载中...</view>
    <view v-else-if="!dataError" class="card">
      <view v-if="detail.product_name || detail.product_display_name" class="field">
        <text>商品</text><text>{{ productName(detail) }}</text>
      </view>
      <view v-for="line in detail.lines || []" :key="line.id" class="field">
        <text>{{ productName(line) }} {{ line.product_spec || '' }}</text>
        <text>{{ qty(line.quantity_before) }} → {{ qty(line.quantity_after) }}（差异 {{ qty(line.quantity_difference) }}）</text>
      </view>
      <view v-for="row in fields" :key="row.key" class="field">
        <text>{{ row.key }}</text><text>{{ row.value }}</text>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import BossDataStatus from '@/components/boss-data-status.vue'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { productName, money, qty } from '@/utils/billing'

const auth = useAuth()
const detail = ref({})
const loading = ref(false)
const meta = ref(null)
const dataError = ref(null)
const labels = { product_spec: '规格', product_code: '商品编码', product_sku: 'SKU',
  total: '账单总额', subtotal: '未税金额', tax_total: '税额', paid_amount: '截至所选日期净收款',
  outstanding_amount: '截至所选日期未收余额', owner_name: '货主', warehouse_name: '仓库',
  base_unit: '基本单位', location_code: '库位', expiry_date: '有效期至', due_date: '到期日',
  invoice_no: '账单号', onhand_qty: '现有数量', available_qty: '可用数量' }
const moneyFields = ['total', 'subtotal', 'tax_total', 'paid_amount', 'outstanding_amount']
const fields = computed(() => Object.entries(detail.value || {})
  .filter(([key]) => !['product_name', 'brand_model', 'product_display_name', 'lines'].includes(key))
  .map(([key, value]) => ({
    key: labels[key] || key,
    value: moneyFields.includes(key) ? money(value, detail.value.currency)
      : typeof value === 'object' && value != null ? JSON.stringify(value) : String(value ?? '-'),
  })))

onLoad(async (query) => {
  if (!auth.ensureAuth()) {
    uni.reLaunch({ url: '/pages/login' })
    return
  }
  loading.value = true
  try {
    const response = await api.bossAlertDetail(query.section, query.item_type, query.id, {
      warehouse: query.warehouse, owner: query.owner,
      date_from: query.date_from, date_to: query.date_to,
    })
    detail.value = response?.detail || {}
    meta.value = response?.meta || null
    dataError.value = null
  } catch (error) {
    dataError.value = error
    detail.value = {}
  } finally { loading.value = false }
})
</script>

<style scoped>
.page { padding: 24rpx; background: #f5f7fb; min-height: 100vh; }
.title { font-size: 38rpx; font-weight: 700; margin-bottom: 18rpx; }
.card { padding: 24rpx; border-radius: 20rpx; background: #fff; }
.field { display: flex; justify-content: space-between; gap: 20rpx; padding: 16rpx 0; border-bottom: 1rpx solid #edf0f5; font-size: 24rpx; }
.field text:first-child { color: #71809c; }
.field text:last-child { text-align: right; word-break: break-all; }
</style>

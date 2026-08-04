<template>
  <view class="page">
    <view class="title">选择出库仓库</view>
    <view class="hint">商品库存和订单履约将按所选仓库计算。</view>

    <view v-if="loading" class="state">正在加载仓库…</view>
    <view v-else-if="errorMessage" class="state error">{{ errorMessage }}</view>
    <view v-else-if="!warehouses.length" class="state empty">
      当前货主未配置可用出库仓库，请联系管理员
    </view>

    <view
      v-for="warehouse in warehouses"
      :key="warehouse.id"
      class="card"
      @click="choose(warehouse)"
    >
      <view class="name">{{ warehouse.name }}</view>
      <view class="code">仓库编码：{{ warehouse.code }}</view>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { useCart } from '@/store/cart'

const auth = useAuth()
const cart = useCart()
const warehouses = ref([])
const loading = ref(true)
const errorMessage = ref('')
let choosing = false
let editing = false

function choose(warehouse) {
  if (choosing || !warehouse?.id) return
  choosing = true
  if (editing && cart.editing_order_id) {
    cart.changeWarehouseForEdit(warehouse)
  } else {
    cart.beginOrder({
      user_id: auth.user?.id,
      owner_id: auth.user?.owner_id,
      warehouse,
    })
  }
  uni.redirectTo({ url: '/pages/customers/select' })
}

async function loadWarehouses() {
  loading.value = true
  errorMessage.value = ''
  auth.ensureAuth()
  if (!auth.user?.id || !auth.user?.owner_id) {
    errorMessage.value = '当前账号没有有效货主范围，请联系管理员'
    loading.value = false
    return
  }

  try {
    const response = await api.warehouses()
    warehouses.value = Array.isArray(response) ? response : []
    if (warehouses.value.length === 1) choose(warehouses.value[0])
  } catch (error) {
    errorMessage.value = error?.message || '加载可用仓库失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

onLoad((query) => {
  editing = query?.edit === '1'
  loadWarehouses()
})
</script>

<style scoped>
.page { padding: 32rpx; }
.title { font-size: 38rpx; font-weight: 700; color: #111827; }
.hint { margin: 12rpx 0 28rpx; color: #6b7280; font-size: 26rpx; }
.state { padding: 48rpx 24rpx; text-align: center; color: #6b7280; }
.error { color: #b91c1c; }
.empty { background: #fff7ed; color: #9a3412; border-radius: 16rpx; }
.card { margin-bottom: 20rpx; padding: 28rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 16rpx; }
.name { font-size: 32rpx; font-weight: 600; color: #111827; }
.code { margin-top: 10rpx; color: #6b7280; font-size: 26rpx; }
</style>

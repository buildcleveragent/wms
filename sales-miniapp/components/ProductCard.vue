<template>
  <view class="product-card" @click="$emit('open', product)">
    <view class="media">
      <image v-if="product.image_url" class="thumb" :src="product.image_url" mode="aspectFill" />
      <view v-else class="thumb placeholder">货</view>
    </view>
    <view class="main">
      <view class="name">{{ product.name }}</view>
      <view v-if="subtitle" class="subtitle">{{ subtitle }}</view>
      <view class="badges">
        <text v-if="product.badges && product.badges.hot" class="badge hot">热卖</text>
        <text v-if="product.badges && product.badges.new" class="badge new">新品</text>
        <text v-if="stockLabel" :class="['badge', stockClass]">{{ stockLabel }}</text>
      </view>
      <view class="foot">
        <view class="price-wrap">
          <PriceText :value="product.price" />
          <text v-if="unitText" class="unit">/{{ unitText }}</text>
        </view>
        <button
          class="add"
          :disabled="isOutOfStock"
          @click.stop="$emit('add', product)"
        >
          {{ addText }}
        </button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed } from 'vue'
import PriceText from './PriceText.vue'

const props = defineProps({
  product: {
    type: Object,
    required: true,
  },
})

defineEmits(['open', 'add'])

const stock = computed(() => props.product.stock || {})
const isOutOfStock = computed(() => stock.value.status === 'OUT')
const stockLabel = computed(() => stock.value.display || stock.value.text || '')
const stockClass = computed(() => {
  const status = String(stock.value.status || '').toUpperCase()
  if (status === 'OUT') return 'stock-out'
  if (status === 'LOW') return 'stock-low'
  return 'stock-ok'
})
const addText = computed(() => (isOutOfStock.value ? '缺货' : '加购'))
const unitText = computed(() => props.product.order_uom || props.product.base_uom || '')
const subtitle = computed(() => {
  const product = props.product || {}
  const parts = [product.spec, product.brand_name || product.brand].filter(Boolean)
  if (!parts.length && unitText.value) parts.push(unitText.value)
  return parts.join(' · ')
})
</script>

<style scoped>
.product-card {
  display: flex;
  gap: 20rpx;
  min-height: 188rpx;
  padding: 18rpx;
  background: #fff;
  border: 1rpx solid #e1e7ef;
  border-radius: 8rpx;
}

.media {
  width: 164rpx;
  height: 164rpx;
  flex-shrink: 0;
}

.thumb {
  width: 164rpx;
  height: 164rpx;
  border-radius: 8rpx;
  background: #eef2f7;
}

.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #0f766e;
  font-size: 42rpx;
  font-weight: 900;
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.name {
  color: #17202a;
  font-size: 28rpx;
  font-weight: 820;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.subtitle {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 23rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8rpx;
  min-height: 38rpx;
  margin-top: 12rpx;
}

.badge {
  height: 34rpx;
  line-height: 34rpx;
  padding: 0 10rpx;
  border-radius: 8rpx;
  background: #eef2f7;
  color: #475569;
  font-size: 20rpx;
}

.hot {
  background: #fff0ec;
  color: #b42318;
}

.new {
  background: #edf7ff;
  color: #2563eb;
}

.stock-ok {
  background: #ecfdf5;
  color: #0f766e;
}

.stock-low {
  background: #fff7ed;
  color: #b45309;
}

.stock-out {
  background: #f1f5f9;
  color: #64748b;
}

.foot {
  margin-top: auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14rpx;
}

.price-wrap {
  min-width: 0;
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4rpx;
}

.unit {
  color: #64748b;
  font-size: 22rpx;
}

.add {
  width: 104rpx;
  height: 56rpx;
  line-height: 56rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 24rpx;
  font-weight: 800;
}

.add::after {
  border: 0;
}

.add[disabled] {
  background: #cbd5e1;
  color: #f8fafc;
}
</style>

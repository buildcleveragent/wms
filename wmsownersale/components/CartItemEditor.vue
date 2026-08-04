<template>
  <view class="cart-item">
    <image class="product-image" :src="item.product_image_url" mode="aspectFill" />
    <view class="item-main">
      <view class="item-heading">
        <view class="product-name">{{ item.name || item.sku || item.product_id }}</view>
        <button class="remove-button" @click="$emit('remove')">删除</button>
      </view>
      <text v-if="item.sku" class="meta">SKU：{{ item.sku }}</text>
      <text v-if="item.spec" class="meta">规格：{{ item.spec }}</text>
      <text v-if="item.gtin" class="meta">条码：{{ item.gtin }}</text>
      <text class="meta">可用库存：{{ item.available ?? '—' }} {{ item.base_unit_name }}</text>

      <view class="editor-grid">
        <view class="field">
          <text>基本单位</text>
          <text class="field-value">{{ item.base_unit_name || '—' }}</text>
        </view>
        <label class="field">
          <text>基本单价</text>
          <input
            class="field-input"
            type="digit"
            inputmode="decimal"
            :value="item.price"
            @input="$emit('price-input', $event.detail.value)"
            @blur="$emit('price-commit')"
          />
        </label>
        <label class="field">
          <text>基本数量</text>
          <input
            :class="['field-input', { invalid: quantityError }]"
            type="digit"
            inputmode="decimal"
            :value="quantityDraft"
            @input="$emit('quantity-input', $event.detail.value)"
            @blur="$emit('quantity-commit')"
          />
          <text v-if="quantityError" class="field-error">{{ quantityError }}</text>
        </label>
        <view class="field amount-field">
          <text>金额</text>
          <text class="amount">¥ {{ amount }}</text>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup>
defineProps({
  item: { type: Object, required: true },
  quantityDraft: { type: String, required: true },
  quantityError: { type: String, default: '' },
  amount: { type: String, default: '0.00' },
})

defineEmits(['remove', 'price-input', 'price-commit', 'quantity-input', 'quantity-commit'])
</script>

<style scoped>
.cart-item { display: flex; gap: 20rpx; margin: 0 20rpx 18rpx; padding: 22rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 16rpx; }
.product-image { width: 144rpx; height: 144rpx; flex: 0 0 144rpx; border-radius: 12rpx; background: #f3f4f6; }
.item-main { flex: 1; min-width: 0; }
.item-heading { display: flex; align-items: flex-start; gap: 12rpx; }
.product-name { flex: 1; font-size: 30rpx; font-weight: 600; color: #111827; }
.remove-button { width: 128rpx; min-height: 88rpx; margin: 0; font-size: 26rpx; color: #b42318; background: #fff; border: 1rpx solid #b42318; }
.meta { display: block; margin-top: 6rpx; font-size: 23rpx; color: #6b7280; }
.editor-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12rpx; margin-top: 16rpx; }
.field { min-width: 0; font-size: 22rpx; color: #6b7280; }
.field-input, .field-value { box-sizing: border-box; display: flex; align-items: center; width: 100%; min-height: 72rpx; margin-top: 6rpx; padding: 8rpx 10rpx; font-size: 26rpx; color: #111827; border: 1rpx solid #d1d5db; border-radius: 8rpx; }
.field-input.invalid { border-color: #b42318; }
.field-error { display: block; margin-top: 6rpx; font-size: 22rpx; color: #b42318; }
.amount-field { text-align: right; }
.amount { display: block; margin-top: 18rpx; font-size: 28rpx; font-weight: 600; color: #b42318; }
@media (max-width: 600px) {
  .product-image { width: 112rpx; height: 112rpx; flex-basis: 112rpx; }
  .editor-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

<template>
  <view class="product-card">
    <image class="product-image" :src="product.product_image_url" mode="aspectFill" />
    <view class="product-main">
      <view class="product-heading">
        <view class="product-name">{{ product.name || product.sku }}</view>
        <button class="add-button" @click="$emit('add')">加入</button>
      </view>
      <text v-if="product.sku" class="meta">SKU：{{ product.sku }}</text>
      <text v-if="product.spec" class="meta">规格：{{ product.spec }}</text>
      <text v-if="product.gtin" class="meta">条码：{{ product.gtin }}</text>
      <text class="stock">可用库存：{{ product.available }} {{ product.base_unit_name }}</text>

      <radio-group
        v-if="product.unitOptions?.length > 1"
        class="unit-options"
        @change="$emit('unit-change', $event.detail.value)"
      >
        <label v-for="(option, index) in product.unitOptions" :key="option.key || index" class="unit-option">
          <radio :value="String(index)" :checked="selectedUnitIndex === index" />
          <text>{{ option.label }}（×{{ option.multiplier }}{{ product.base_unit_name }}）</text>
        </label>
      </radio-group>

      <view class="editor-grid">
        <label class="field">
          <text>出货数量</text>
          <input
            class="field-input"
            type="digit"
            inputmode="decimal"
            :value="quantity"
            placeholder="请输入"
            @input="$emit('quantity-input', $event.detail.value)"
          />
        </label>
        <view class="field">
          <text>基本数量</text>
          <text class="field-value">{{ baseQuantity }}</text>
        </view>
        <label class="field">
          <text>基本单价</text>
          <input
            class="field-input"
            type="digit"
            inputmode="decimal"
            :value="product.price"
            @input="$emit('price-input', $event.detail.value)"
            @blur="$emit('price-commit')"
          />
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
  product: { type: Object, required: true },
  quantity: { type: String, default: '' },
  baseQuantity: { type: Number, default: 0 },
  selectedUnitIndex: { type: Number, default: 0 },
  amount: { type: String, default: '0.00' },
})

defineEmits(['add', 'unit-change', 'quantity-input', 'price-input', 'price-commit'])
</script>

<style scoped>
.product-card {
  display: flex;
  gap: 20rpx;
  margin: 0 20rpx 18rpx;
  padding: 22rpx;
  background: #fff;
  border: 1rpx solid #e5e7eb;
  border-radius: 16rpx;
}
.product-image { width: 144rpx; height: 144rpx; flex: 0 0 144rpx; border-radius: 12rpx; background: #f3f4f6; }
.product-main { flex: 1; min-width: 0; }
.product-heading { display: flex; align-items: flex-start; gap: 12rpx; }
.product-name { flex: 1; font-size: 30rpx; font-weight: 600; color: #111827; }
.add-button { width: 128rpx; min-height: 88rpx; margin: 0; font-size: 26rpx; color: #fff; background: #2563eb; }
.meta, .stock { display: block; margin-top: 6rpx; font-size: 23rpx; color: #6b7280; }
.stock { color: #166534; }
.unit-options { margin-top: 14rpx; padding: 12rpx; background: #f8fafc; border-radius: 10rpx; }
.unit-option { display: flex; align-items: center; min-height: 60rpx; font-size: 24rpx; color: #374151; }
.editor-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12rpx; margin-top: 16rpx; }
.field { min-width: 0; font-size: 22rpx; color: #6b7280; }
.field-input, .field-value { box-sizing: border-box; display: flex; align-items: center; width: 100%; min-height: 72rpx; margin-top: 6rpx; padding: 8rpx 10rpx; font-size: 26rpx; color: #111827; border: 1rpx solid #d1d5db; border-radius: 8rpx; }
.amount-field { text-align: right; }
.amount { display: block; margin-top: 18rpx; font-size: 28rpx; font-weight: 600; color: #b42318; }
@media (max-width: 600px) {
  .product-card { align-items: flex-start; }
  .product-image { width: 112rpx; height: 112rpx; flex-basis: 112rpx; }
  .editor-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

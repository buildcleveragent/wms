<template>
  <view class="page address-page">
    <view v-if="rows.length" class="list">
      <view v-for="item in rows" :key="item.id" class="row">
        <AddressCard :address="item" @select="selectAddress(item)" />
        <button class="edit" @click="edit(item)">编辑</button>
      </view>
    </view>
    <EmptyState v-else text="暂无收货地址" />
    <button class="add" @click="create">新增地址</button>
  </view>
</template>

<script setup>
import { onLoad, onShow } from '@dcloudio/uni-app'
import { ref } from 'vue'
import AddressCard from '../../components/AddressCard.vue'
import EmptyState from '../../components/EmptyState.vue'
import { addressService } from '../../services/address'

const rows = ref([])
const selectable = ref(false)
const ownerId = ref('')
const CHECKOUT_OWNER_KEY = 'sale_mini_address_owner_id'
const EDIT_OWNER_KEY = 'sale_mini_address_edit_owner_id'

async function load() {
  const params = ownerId.value ? { owner_id: ownerId.value } : {}
  rows.value = await addressService.list(params)
}

function selectAddress(item) {
  if (!selectable.value) return
  uni.setStorageSync('sale_mini_selected_address', item)
  uni.navigateBack()
}

function edit(item) {
  const addressOwnerId = item.owner_id || ownerId.value || ''
  if (addressOwnerId) {
    uni.setStorageSync(EDIT_OWNER_KEY, addressOwnerId)
  } else {
    uni.removeStorageSync(EDIT_OWNER_KEY)
  }
  uni.navigateTo({ url: `/pages/address-edit/address-edit?id=${item.id}` })
}

function create() {
  if (ownerId.value) {
    uni.setStorageSync(EDIT_OWNER_KEY, ownerId.value)
  } else {
    uni.removeStorageSync(EDIT_OWNER_KEY)
  }
  uni.navigateTo({ url: '/pages/address-edit/address-edit' })
}

onLoad((query = {}) => {
  selectable.value = query.select === '1'
  ownerId.value = selectable.value ? uni.getStorageSync(CHECKOUT_OWNER_KEY) || '' : ''
})

onShow(() => {
  load().catch((err) => {
    uni.showToast({ title: err.message || '地址加载失败', icon: 'none' })
  })
})
</script>

<style scoped>
.address-page {
  padding-bottom: 130rpx;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.row {
  position: relative;
}

.edit {
  position: absolute;
  right: 18rpx;
  bottom: 18rpx;
  width: 72rpx;
  height: 44rpx;
  line-height: 44rpx;
  padding: 0;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #475569;
  font-size: 22rpx;
}

.edit::after {
  border: 0;
}

.add {
  position: fixed;
  left: 24rpx;
  right: 24rpx;
  bottom: 24rpx;
  height: 84rpx;
  line-height: 84rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 28rpx;
  font-weight: 800;
}

.add::after {
  border: 0;
}
</style>

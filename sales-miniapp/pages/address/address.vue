<template>
  <view class="page address-page">
    <scroll-view v-if="showMerchantSwitch" class="merchant-scroll" scroll-x>
      <view class="merchant-row">
        <view
          v-for="merchant in merchantOptions"
          :key="merchant.id"
          :class="['merchant-chip', Number(ownerId) === Number(merchant.id) && 'active']"
          @click="selectMerchant(merchant.id)"
        >
          {{ merchant.name }}
        </view>
      </view>
    </scroll-view>
    <view v-else-if="currentMerchantName" class="merchant-title">{{ currentMerchantName }}</view>
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
import { computed, ref } from 'vue'
import AddressCard from '../../components/AddressCard.vue'
import EmptyState from '../../components/EmptyState.vue'
import { addressService } from '../../services/address'
import { useSessionStore } from '../../stores/session'

const session = useSessionStore()
const rows = ref([])
const selectable = ref(false)
const ownerId = ref('')
const merchantOptions = ref([])
const loadedProfile = ref(false)

const showMerchantSwitch = computed(() => !selectable.value && merchantOptions.value.length > 1)
const currentMerchantName = computed(() => {
  const row = merchantOptions.value.find((item) => Number(item.id) === Number(ownerId.value))
  return row ? `${row.name} 地址` : ''
})

function bindingsFromProfile(profile) {
  const current = profile || {}
  const bindings = Array.isArray(current.bindings) ? current.bindings : []
  const rows = bindings
    .map((binding) => binding.owner)
    .filter((owner) => owner && owner.id)
  if (!rows.length && current.owner && current.owner.id) rows.push(current.owner)
  const seen = new Set()
  return rows.filter((owner) => {
    const key = String(owner.id)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

async function loadMerchantOptions() {
  if (!loadedProfile.value) {
    await session.fetchProfile()
    loadedProfile.value = true
  }
  merchantOptions.value = bindingsFromProfile(session.profile)
  if (!ownerId.value && merchantOptions.value.length) {
    ownerId.value = String(merchantOptions.value[0].id)
  }
}

async function load() {
  await loadMerchantOptions()
  rows.value = await addressService.list({ owner_id: ownerId.value })
}

function selectMerchant(id) {
  if (Number(ownerId.value) === Number(id)) return
  ownerId.value = String(id || '')
  rows.value = []
  load().catch((err) => {
    uni.showToast({ title: err.message || '地址加载失败', icon: 'none' })
  })
}

function selectAddress(item) {
  if (!selectable.value) return
  uni.setStorageSync('sale_mini_selected_address', item)
  uni.navigateBack()
}

function edit(item) {
  uni.navigateTo({ url: `/pages/address-edit/address-edit?id=${item.id}&owner_id=${ownerId.value}` })
}

function create() {
  uni.navigateTo({ url: `/pages/address-edit/address-edit?owner_id=${ownerId.value}` })
}

onLoad((query = {}) => {
  selectable.value = query.select === '1'
  ownerId.value = query.owner_id || ''
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

.merchant-scroll {
  margin-bottom: 18rpx;
  white-space: nowrap;
}

.merchant-row {
  display: flex;
  gap: 10rpx;
}

.merchant-chip {
  min-width: 150rpx;
  max-width: 260rpx;
  height: 62rpx;
  padding: 0 18rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #475569;
  font-size: 24rpx;
  font-weight: 750;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
}

.merchant-chip.active {
  border-color: #0f766e;
  background: #edf8f5;
  color: #0f766e;
}

.merchant-title {
  margin-bottom: 18rpx;
  color: #17202a;
  font-size: 30rpx;
  font-weight: 850;
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

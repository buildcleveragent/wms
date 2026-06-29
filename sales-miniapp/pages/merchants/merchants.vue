<template>
  <view class="page merchant-page">
    <view class="page-title">全部商家</view>
    <view class="search-row">
      <input class="input search-input" v-model="keyword" placeholder="搜索商家名称" confirm-type="search" />
      <button class="search-btn" @click="load">刷新</button>
    </view>

    <view class="summary">
      <view>
        <view class="summary-value">{{ filteredRows.length }}</view>
        <view class="summary-label">在售商家</view>
      </view>
      <view>
        <view class="summary-value">{{ totalProducts }}</view>
        <view class="summary-label">上架商品</view>
      </view>
      <view>
        <view class="summary-value">{{ hotMerchants }}</view>
        <view class="summary-label">热卖商家</view>
      </view>
    </view>

    <view class="filters">
      <button :class="['filter', mode === 'all' && 'active']" @click="mode = 'all'">全部</button>
      <button :class="['filter', mode === 'hot' && 'active']" @click="mode = 'hot'">热卖</button>
      <button :class="['filter', mode === 'recommended' && 'active']" @click="mode = 'recommended'">推荐</button>
    </view>

    <view v-if="filteredRows.length" class="merchant-list">
      <view v-for="merchant in filteredRows" :key="merchant.id" class="merchant-card" @click="openMerchant(merchant)">
        <view class="avatar">{{ merchant.name.slice(0, 1) }}</view>
        <view class="main">
          <view class="name">{{ merchant.name }}</view>
          <view class="meta">{{ merchant.product_count }} 件在售 · {{ merchant.hot_count || 0 }} 件热卖</view>
          <view class="tags">
            <text v-if="Number(merchant.recommended_count || 0) > 0" class="tag">推荐</text>
            <text v-if="Number(merchant.hot_count || 0) > 0" class="tag hot">热卖</text>
            <text class="tag quiet">配送/自提</text>
          </view>
        </view>
        <view class="enter">进店</view>
      </view>
    </view>
    <EmptyState v-else :text="loading ? '加载中' : '暂无商家'" />
  </view>
</template>

<script setup>
import { onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import { productService } from '../../services/product'

const rows = ref([])
const keyword = ref('')
const mode = ref('all')
const loading = ref(false)

const filteredRows = computed(() => {
  const word = keyword.value.trim().toLowerCase()
  return rows.value.filter((item) => {
    if (word && !String(item.name || '').toLowerCase().includes(word)) return false
    if (mode.value === 'hot') return Number(item.hot_count || 0) > 0
    if (mode.value === 'recommended') return Number(item.recommended_count || 0) > 0
    return true
  })
})
const totalProducts = computed(() => filteredRows.value.reduce((sum, item) => sum + Number(item.product_count || 0), 0))
const hotMerchants = computed(() => filteredRows.value.filter((item) => Number(item.hot_count || 0) > 0).length)

async function load() {
  if (loading.value) return
  loading.value = true
  try {
    rows.value = await productService.merchants()
  } catch (err) {
    uni.showToast({ title: err.message || '商家加载失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function openMerchant(merchant) {
  if (!merchant || !merchant.id) return
  uni.navigateTo({ url: `/pages/merchant-detail/merchant-detail?owner_id=${merchant.id}` })
}

onShow(() => {
  if (!rows.value.length) load()
})

onPullDownRefresh(async () => {
  try {
    await load()
  } finally {
    uni.stopPullDownRefresh()
  }
})
</script>

<style scoped>
.merchant-page {
  padding-bottom: 48rpx;
}

.page-title {
  margin-bottom: 18rpx;
  color: #17202a;
  font-size: 36rpx;
  font-weight: 900;
}

.search-row {
  display: flex;
  gap: 12rpx;
}

.search-input {
  flex: 1;
}

.search-btn {
  width: 112rpx;
  height: 80rpx;
  line-height: 80rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 25rpx;
  font-weight: 750;
}

.search-btn::after {
  border: 0;
}

.summary {
  margin-top: 18rpx;
  padding: 22rpx 12rpx;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
  text-align: center;
}

.summary-value {
  color: #0f766e;
  font-size: 34rpx;
  font-weight: 900;
}

.summary-label {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 23rpx;
}

.filters {
  margin: 18rpx 0;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10rpx;
}

.filter {
  height: 64rpx;
  line-height: 64rpx;
  padding: 0;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #475569;
  font-size: 24rpx;
}

.filter::after {
  border: 0;
}

.filter.active {
  border-color: #0f766e;
  color: #0f766e;
  background: #edf8f5;
}

.merchant-list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.merchant-card {
  padding: 20rpx;
  display: flex;
  align-items: center;
  gap: 16rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.avatar {
  width: 76rpx;
  height: 76rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8rpx;
  background: #edf8f5;
  color: #0f766e;
  font-size: 32rpx;
  font-weight: 900;
  flex-shrink: 0;
}

.main {
  min-width: 0;
  flex: 1;
}

.name {
  color: #17202a;
  font-size: 30rpx;
  font-weight: 850;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 23rpx;
}

.tags {
  margin-top: 10rpx;
  display: flex;
  flex-wrap: wrap;
  gap: 8rpx;
}

.tag {
  height: 38rpx;
  padding: 0 10rpx;
  display: flex;
  align-items: center;
  border-radius: 8rpx;
  background: #edf8f5;
  color: #0f766e;
  font-size: 20rpx;
}

.tag.hot {
  background: #fff1f2;
  color: #b42318;
}

.tag.quiet {
  background: #f8fafc;
  color: #64748b;
}

.enter {
  width: 76rpx;
  height: 52rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 23rpx;
  font-weight: 750;
  flex-shrink: 0;
}
</style>

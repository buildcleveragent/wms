<template>
  <view class="page">
    <view class="summary">
      <view>
        <text class="score">{{ summary.average_overall || '0.0' }}</text>
        <text class="score-unit">分</text>
      </view>
      <view class="summary-detail">
        <view>共 {{ summary.count || 0 }} 条评价</view>
        <view class="averages">质量 {{ summary.average_quality || '0.0' }} · 配送 {{ summary.average_delivery || '0.0' }}</view>
      </view>
    </view>

    <scroll-view class="filter-scroll" scroll-x :show-scrollbar="false">
      <view class="filters">
        <button
          v-for="item in filters"
          :key="item.key"
          :class="['filter', { active: activeFilter === item.key }]"
          @click="setFilter(item.key)"
        >
          {{ item.label }}
        </button>
      </view>
    </scroll-view>

    <view class="sorts">
      <button
        v-for="item in sorts"
        :key="item.key"
        :class="['sort', { active: ordering === item.key }]"
        @click="setOrdering(item.key)"
      >
        {{ item.label }}
      </button>
    </view>

    <view v-if="reviews.length" class="review-list">
      <ReviewCard v-for="item in reviews" :key="item.id" :review="item" />
      <view class="load-state">{{ hasMore ? (loading ? '加载中' : '继续上拉') : '已经到底了' }}</view>
    </view>
    <EmptyState v-else-if="!loading" text="暂无评价" />
  </view>
</template>

<script setup>
import { onLoad, onPullDownRefresh, onReachBottom } from '@dcloudio/uni-app'
import { ref } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import ReviewCard from '../../components/ReviewCard.vue'
import { reviewService } from '../../services/review'

const productId = ref('')
const configId = ref('')
const reviews = ref([])
const summary = ref({})
const page = ref(1)
const total = ref(0)
const loading = ref(false)
const activeFilter = ref('all')
const ordering = ref('newest')

const filters = [
  { key: 'all', label: '全部' },
  { key: 'images', label: '有图' },
  { key: '5', label: '5星' },
  { key: '4', label: '4星' },
  { key: '3', label: '3星' },
  { key: '2', label: '2星' },
  { key: '1', label: '1星' },
]
const sorts = [
  { key: 'newest', label: '最新' },
  { key: 'highest', label: '高分' },
  { key: 'lowest', label: '低分' },
]

const hasMore = ref(false)

function params() {
  const data = {
    config_id: configId.value,
    page: page.value,
    page_size: 10,
    ordering: ordering.value,
  }
  if (activeFilter.value === 'images') data.has_images = 1
  if (/^[1-5]$/.test(activeFilter.value)) data.score = activeFilter.value
  return data
}

async function load(reset = false) {
  if (loading.value || !productId.value) return
  if (reset) {
    page.value = 1
    reviews.value = []
  }
  loading.value = true
  try {
    const data = await reviewService.list(productId.value, params())
    reviews.value = reset ? (data.results || []) : reviews.value.concat(data.results || [])
    summary.value = data.summary || {}
    total.value = Number(data.count || 0)
    hasMore.value = reviews.value.length < total.value
    if (hasMore.value) page.value += 1
  } catch (err) {
    uni.showToast({ title: err.message || '评价加载失败', icon: 'none' })
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function setFilter(key) {
  if (activeFilter.value === key) return
  activeFilter.value = key
  load(true)
}

function setOrdering(key) {
  if (ordering.value === key) return
  ordering.value = key
  load(true)
}

onLoad((query) => {
  productId.value = query.product_id || ''
  configId.value = query.config_id || ''
  load(true)
})

onPullDownRefresh(() => load(true))
onReachBottom(() => {
  if (hasMore.value) load(false)
})
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 18rpx;
  background: #f4f6f8;
}

.summary {
  display: flex;
  align-items: center;
  gap: 24rpx;
  padding: 24rpx;
  background: #fff;
  border: 1rpx solid #e1e7ef;
  border-radius: 8rpx;
}

.score {
  color: #b42318;
  font-size: 56rpx;
  font-weight: 900;
}

.score-unit,
.averages {
  color: #64748b;
  font-size: 23rpx;
}

.summary-detail {
  color: #17202a;
  font-size: 27rpx;
  line-height: 1.8;
}

.filter-scroll {
  width: 100%;
  margin-top: 16rpx;
  white-space: nowrap;
}

.filters,
.sorts {
  display: flex;
  gap: 10rpx;
}

.filter,
.sort {
  height: 58rpx;
  padding: 0 22rpx;
  border: 0;
  border-radius: 8rpx;
  background: #fff;
  color: #475569;
  font-size: 23rpx;
  line-height: 58rpx;
}

.filter::after,
.sort::after {
  border: 0;
}

.filter.active,
.sort.active {
  background: #e8f3ff;
  color: #1677ff;
  font-weight: 700;
}

.sorts {
  justify-content: flex-end;
  margin-top: 14rpx;
}

.review-list {
  margin-top: 16rpx;
  padding: 0 22rpx;
  background: #fff;
  border: 1rpx solid #e1e7ef;
  border-radius: 8rpx;
}

.load-state {
  padding: 24rpx;
  color: #94a3b8;
  font-size: 22rpx;
  text-align: center;
}
</style>

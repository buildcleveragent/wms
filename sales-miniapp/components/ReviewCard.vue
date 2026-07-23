<template>
  <view class="review-card">
    <view class="review-head">
      <view class="identity">
        <image v-if="review.avatar_url" class="avatar" :src="review.avatar_url" mode="aspectFill" />
        <view v-else class="avatar fallback">人</view>
        <view>
          <view class="name-row">
            <text class="reviewer">{{ review.display_name || '匿名用户' }}</text>
            <text v-if="review.verified_purchase" class="verified">已购买</text>
          </view>
          <StarRating :model-value="Number(review.overall_score || 0)" readonly />
        </view>
      </view>
      <text class="date">{{ dateText(review.published_at) }}</text>
    </view>
    <view class="dimension-row">
      <text>质量 {{ review.quality_score }}星</text>
      <text>配送 {{ review.delivery_score }}星</text>
      <text>综合 {{ review.overall_score }}星</text>
    </view>
    <view class="content">{{ review.content || '用户未填写文字评价' }}</view>
    <view v-if="review.images && review.images.length" class="images">
      <image
        v-for="(item, index) in review.images"
        :key="item.id || item.url"
        class="review-image"
        :src="item.url"
        mode="aspectFill"
        @click.stop="preview(index)"
      />
    </view>
  </view>
</template>

<script setup>
import StarRating from './StarRating.vue'

const props = defineProps({
  review: { type: Object, required: true },
})

function dateText(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 10)
}

function preview(index) {
  const urls = (props.review.images || []).map((item) => item.url).filter(Boolean)
  if (!urls.length) return
  uni.previewImage({ current: urls[index], urls })
}
</script>

<style scoped>
.review-card {
  padding: 22rpx 0;
  border-bottom: 1rpx solid #e5e7eb;
  background: #fff;
}

.review-head,
.identity,
.name-row,
.dimension-row,
.images {
  display: flex;
  align-items: center;
}

.review-head {
  justify-content: space-between;
  gap: 16rpx;
}

.identity {
  min-width: 0;
  gap: 14rpx;
}

.avatar {
  width: 64rpx;
  height: 64rpx;
  flex-shrink: 0;
  border-radius: 50%;
  background: #eef2f7;
}

.fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  font-size: 24rpx;
}

.name-row {
  gap: 10rpx;
}

.reviewer {
  max-width: 240rpx;
  overflow: hidden;
  color: #17202a;
  font-size: 25rpx;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.verified {
  padding: 2rpx 8rpx;
  border-radius: 6rpx;
  background: #e8f3ff;
  color: #1677ff;
  font-size: 19rpx;
}

.date {
  flex-shrink: 0;
  color: #94a3b8;
  font-size: 21rpx;
}

.dimension-row {
  flex-wrap: wrap;
  gap: 12rpx;
  margin-top: 14rpx;
  color: #7c2d12;
  font-size: 21rpx;
}

.content {
  margin-top: 14rpx;
  color: #1f2937;
  font-size: 26rpx;
  line-height: 1.6;
}

.images {
  flex-wrap: wrap;
  gap: 10rpx;
  margin-top: 16rpx;
}

.review-image {
  width: 180rpx;
  height: 180rpx;
  border-radius: 6rpx;
  background: #eef2f7;
}
</style>

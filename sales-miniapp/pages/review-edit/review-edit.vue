<template>
  <view class="page">
    <view v-if="review.id" class="content">
      <view class="product-row">
        <image v-if="review.product_image_url" class="product-image" :src="review.product_image_url" mode="aspectFill" />
        <view v-else class="product-image placeholder">货</view>
        <view class="product-main">
          <view class="product-name">{{ review.product_name }}</view>
          <view v-if="review.product_spec" class="product-spec">{{ review.product_spec }}</view>
        </view>
      </view>

      <view class="score-panel">
        <view class="score-row">
          <text>商品质量</text>
          <StarRating v-model="form.quality_score" label="商品质量" />
        </view>
        <view class="score-row">
          <text>{{ deliveryLabel }}</text>
          <StarRating v-model="form.delivery_score" :label="deliveryLabel" />
        </view>
        <view class="score-row">
          <text>综合满意度</text>
          <StarRating v-model="form.overall_score" label="综合满意度" />
        </view>
      </view>

      <view class="editor">
        <textarea
          v-model="form.content"
          class="textarea"
          maxlength="1000"
          placeholder="说说商品和服务体验"
        />
        <view class="counter">{{ form.content.length }}/1000</view>
      </view>

      <view class="image-panel">
        <view class="image-grid">
          <view v-for="item in review.images || []" :key="item.id" class="image-item">
            <image class="image" :src="item.url" mode="aspectFill" @click="previewImage(item.url)" />
            <button class="remove" aria-label="删除图片" @click="removeImage(item)">×</button>
          </view>
          <button v-if="(review.images || []).length < 6" class="add-image" aria-label="添加图片" @click="chooseImages">
            <text class="plus">+</text>
            <text class="image-count">{{ (review.images || []).length }}/6</text>
          </button>
        </view>
      </view>

      <view class="anonymous-row">
        <text>匿名评价</text>
        <switch :checked="form.is_anonymous" color="#1677ff" @change="form.is_anonymous = $event.detail.value" />
      </view>

      <view v-if="review.rejection_reason" class="reject">驳回原因：{{ review.rejection_reason }}</view>
      <button class="submit" :loading="submitting" @click="submitReview">提交审核</button>
    </view>
    <EmptyState v-else text="评价加载中" />
  </view>
</template>

<script setup>
import { onLoad } from '@dcloudio/uni-app'
import { computed, reactive, ref } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import StarRating from '../../components/StarRating.vue'
import { reviewService } from '../../services/review'

const review = ref({})
const deliveryMethod = ref('')
const submitting = ref(false)
const uploading = ref(false)
const form = reactive({
  quality_score: 5,
  delivery_score: 5,
  overall_score: 5,
  content: '',
  is_anonymous: true,
})

const deliveryLabel = computed(() => (deliveryMethod.value === 'PICKUP' ? '自提服务' : '配送服务'))

function sync(data) {
  review.value = data || {}
  form.quality_score = Number(data.quality_score || 5)
  form.delivery_score = Number(data.delivery_score || 5)
  form.overall_score = Number(data.overall_score || 5)
  form.content = data.content || ''
  form.is_anonymous = data.is_anonymous !== false
}

function payload() {
  return {
    quality_score: form.quality_score,
    delivery_score: form.delivery_score,
    overall_score: form.overall_score,
    content: form.content,
    is_anonymous: form.is_anonymous,
  }
}

async function saveDraft() {
  if (!review.value.id) return
  sync(await reviewService.updateDraft(review.value.id, payload()))
}

async function chooseImages() {
  if (uploading.value) return
  const remaining = 6 - (review.value.images || []).length
  if (remaining <= 0) return
  uni.chooseImage({
    count: remaining,
    sizeType: ['compressed'],
    sourceType: ['album', 'camera'],
    success: async (result) => {
      uploading.value = true
      try {
        await saveDraft()
        for (const path of result.tempFilePaths || []) {
          sync(await reviewService.uploadImage(review.value.id, path))
        }
      } catch (err) {
        uni.showToast({ title: err.message || '图片上传失败', icon: 'none' })
      } finally {
        uploading.value = false
      }
    },
  })
}

async function removeImage(item) {
  try {
    await reviewService.deleteImage(review.value.id, item.id)
    review.value.images = (review.value.images || []).filter((row) => row.id !== item.id)
  } catch (err) {
    uni.showToast({ title: err.message || '删除失败', icon: 'none' })
  }
}

function previewImage(url) {
  const urls = (review.value.images || []).map((item) => item.url)
  uni.previewImage({ current: url, urls })
}

async function submitReview() {
  if (submitting.value || uploading.value) return
  submitting.value = true
  try {
    await saveDraft()
    sync(await reviewService.submit(review.value.id))
    uni.showToast({ title: '评价已提交审核', icon: 'success' })
    setTimeout(() => uni.navigateBack(), 500)
  } catch (err) {
    uni.showToast({ title: err.message || '提交失败', icon: 'none' })
  } finally {
    submitting.value = false
  }
}

onLoad(async (query) => {
  deliveryMethod.value = query.delivery_method || ''
  try {
    if (query.review_id) {
      sync(await reviewService.draft(query.review_id))
      return
    }
    sync(await reviewService.createDraft({
      order_line_id: Number(query.line_id),
      ...payload(),
    }))
  } catch (err) {
    uni.showToast({ title: err.message || '无法创建评价', icon: 'none' })
  }
})
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 18rpx;
  background: #f4f6f8;
}

.product-row,
.score-panel,
.editor,
.image-panel,
.anonymous-row {
  background: #fff;
  border: 1rpx solid #e1e7ef;
  border-radius: 8rpx;
}

.product-row {
  display: flex;
  gap: 18rpx;
  padding: 20rpx;
}

.product-image {
  width: 132rpx;
  height: 132rpx;
  flex-shrink: 0;
  border-radius: 6rpx;
  background: #eef2f7;
}

.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #0f766e;
  font-size: 40rpx;
  font-weight: 800;
}

.product-main {
  min-width: 0;
}

.product-name {
  color: #17202a;
  font-size: 29rpx;
  font-weight: 800;
  line-height: 1.5;
}

.product-spec {
  margin-top: 8rpx;
  color: #64748b;
  font-size: 23rpx;
}

.score-panel,
.editor,
.image-panel,
.anonymous-row {
  margin-top: 16rpx;
  padding: 20rpx;
}

.score-row,
.anonymous-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  color: #334155;
  font-size: 26rpx;
}

.score-row + .score-row {
  margin-top: 18rpx;
}

.textarea {
  width: 100%;
  min-height: 230rpx;
  color: #17202a;
  font-size: 27rpx;
  line-height: 1.6;
}

.counter {
  color: #94a3b8;
  font-size: 21rpx;
  text-align: right;
}

.image-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12rpx;
}

.image-item,
.image,
.add-image {
  width: 100%;
  aspect-ratio: 1;
}

.image-item {
  position: relative;
}

.image,
.add-image {
  border-radius: 6rpx;
  background: #eef2f7;
}

.remove {
  position: absolute;
  top: 6rpx;
  right: 6rpx;
  width: 44rpx;
  height: 44rpx;
  min-width: 44rpx;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: rgba(15, 23, 42, 0.75);
  color: #fff;
  font-size: 34rpx;
  line-height: 40rpx;
}

.remove::after,
.add-image::after {
  border: 0;
}

.add-image {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border: 1rpx dashed #94a3b8;
  color: #64748b;
}

.plus {
  font-size: 48rpx;
  line-height: 1;
}

.image-count {
  margin-top: 6rpx;
  font-size: 20rpx;
}

.reject {
  margin-top: 16rpx;
  padding: 18rpx;
  border: 1rpx solid #fecaca;
  border-radius: 8rpx;
  background: #fff1f2;
  color: #b42318;
  font-size: 24rpx;
}

.submit {
  height: 84rpx;
  margin-top: 22rpx;
  border: 0;
  border-radius: 8rpx;
  background: #1677ff;
  color: #fff;
  font-size: 29rpx;
  font-weight: 800;
  line-height: 84rpx;
}

.submit::after {
  border: 0;
}
</style>

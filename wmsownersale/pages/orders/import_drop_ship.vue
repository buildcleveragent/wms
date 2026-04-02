<template>
  <view class="page">
    <view class="card">
      <view class="title">一件代发批量导入</view>
      <view class="desc">
        请上传业务员填写好的 Excel 文件。系统会按行逐行生成出库单，每行一个散户订单。
      </view>

      <view class="tips">
        <view>模板要求：</view>
        <view>1. 每行 1 个订单</view>
        <view>2. 优先填写“商家编码”匹配系统 SKU</view>
        <view>3. “订单编号”建议必填，用于防重复</view>
      </view>

      <view class="file-box">
        <view class="label">已选文件</view>
        <view class="file-name">{{ fileName || '未选择文件' }}</view>
      </view>

      <view class="btn-row">
        <button class="btn btn-outline" @click="chooseExcel" :disabled="uploading">
          选择 Excel
        </button>
        <button class="btn" @click="uploadExcel" :disabled="!filePath || uploading">
          {{ uploading ? '上传中...' : '上传并导入' }}
        </button>
      </view>
    </view>

    <view v-if="result" class="card result-card">
      <view class="title">导入结果</view>

      <view class="result-summary">
        <view>总行数：{{ result.total_rows || 0 }}</view>
        <view>成功：{{ result.success_count || 0 }}</view>
        <view>跳过：{{ result.skip_count || 0 }}</view>
        <view>失败：{{ result.fail_count || 0 }}</view>
      </view>

      <view v-if="result.successes?.length" class="result-block">
        <view class="sub-title">成功</view>
        <view
          v-for="(it, idx) in result.successes"
          :key="'s' + idx"
          class="result-item ok"
        >
          第{{ it.row }}行：{{ it.order_no || it.order_id }}（{{ it.src_bill_no }}）
        </view>
      </view>

      <view v-if="result.skips?.length" class="result-block">
        <view class="sub-title">跳过</view>
        <view
          v-for="(it, idx) in result.skips"
          :key="'k' + idx"
          class="result-item warn"
        >
          第{{ it.row }}行：{{ it.reason }}
        </view>
      </view>

      <view v-if="result.errors?.length" class="result-block">
        <view class="sub-title">失败</view>
        <view
          v-for="(it, idx) in result.errors"
          :key="'e' + idx"
          class="result-item err"
        >
          第{{ it.row }}行：{{ it.reason }}
        </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '@/utils/request.js'

const filePath = ref('')
const fileName = ref('')
const uploading = ref(false)
const result = ref(null)

function chooseExcel() {
  uni.chooseFile({
    count: 1,
    extension: ['.xlsx', '.xls'],
    success: (res) => {
      const path = res?.tempFilePaths?.[0] || ''
      const file = res?.tempFiles?.[0] || {}

      filePath.value = path
      fileName.value = file.name || path.split('/').pop() || '已选择文件'
    },
    fail: (err) => {
      console.error('chooseFile fail', err)
      uni.showToast({ title: '选择文件失败', icon: 'none' })
    }
  })
}

async function uploadExcel() {
  if (!filePath.value) {
    uni.showToast({ title: '请先选择 Excel 文件', icon: 'none' })
    return
  }

  try {
    uploading.value = true
    result.value = null

    const res = await api.importDropShipExcel(filePath.value)
    result.value = res

    uni.showToast({
      title: `成功${res?.success_count || 0}条`,
      icon: 'none'
    })
  } catch (e) {
    console.error('import excel fail', e)
    const msg =
      e?.data?.detail ||
      e?.errMsg ||
      '导入失败'
    uni.showToast({ title: msg, icon: 'none' })
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 20rpx;
  background: #f5f7fa;
  box-sizing: border-box;
}

.card {
  background: #fff;
  border-radius: 16rpx;
  padding: 24rpx;
  margin-bottom: 20rpx;
  box-sizing: border-box;
}

.title {
  font-size: 34rpx;
  font-weight: 600;
  color: #111827;
  margin-bottom: 16rpx;
}

.desc {
  font-size: 26rpx;
  color: #4b5563;
  line-height: 1.7;
  margin-bottom: 16rpx;
}

.tips {
  font-size: 24rpx;
  color: #6b7280;
  line-height: 1.8;
  padding: 16rpx;
  background: #f9fafb;
  border-radius: 12rpx;
  margin-bottom: 20rpx;
}

.file-box {
  padding: 16rpx;
  background: #f9fafb;
  border-radius: 12rpx;
  margin-bottom: 20rpx;
}

.label {
  font-size: 24rpx;
  color: #6b7280;
  margin-bottom: 8rpx;
}

.file-name {
  font-size: 28rpx;
  color: #111827;
  word-break: break-all;
}

.btn-row {
  display: flex;
  gap: 16rpx;
}

.btn {
  flex: 1;
  height: 80rpx;
  line-height: 80rpx;
  font-size: 28rpx;
  border-radius: 12rpx;
}

.btn-outline {
  background: #fff;
  color: #2563eb;
  border: 1rpx solid #2563eb;
}

.result-card {
  margin-top: 20rpx;
}

.result-summary {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12rpx;
  margin-bottom: 20rpx;
  font-size: 26rpx;
  color: #111827;
}

.result-block {
  margin-top: 16rpx;
}

.sub-title {
  font-size: 28rpx;
  font-weight: 600;
  margin-bottom: 10rpx;
}

.result-item {
  padding: 12rpx 14rpx;
  border-radius: 10rpx;
  margin-bottom: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  word-break: break-all;
}

.ok {
  background: #ecfdf5;
  color: #065f46;
}

.warn {
  background: #fffbeb;
  color: #92400e;
}

.err {
  background: #fef2f2;
  color: #991b1b;
}
</style>
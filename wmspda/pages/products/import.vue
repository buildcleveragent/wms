<template>
  <view class="page product-import-page">
    <view v-if="profileReady && !canImport" class="card denied-card">
      <view class="card-title">暂无商品导入权限</view>
      <view class="muted">请联系管理员授予商品新增权限；跨货主导入还需要跨货主管理权限。</view>
    </view>

    <template v-else>
      <view class="card">
        <view class="card-title">1. 下载标准模板</view>
        <view class="muted">请使用系统模板填写，只支持 .xlsx，单次最多 1000 条、5 MB。</view>
        <button class="primary-btn outline-btn" :disabled="busy" @click="downloadTemplate">
          {{ downloading ? '模板下载中…' : '下载 Excel 模板' }}
        </button>
      </view>

      <view class="card">
        <view class="card-title">2. 选择填写好的文件</view>
        <view class="file-box" :class="{ selected: selectedFile.path }">
          <view class="file-icon">📄</view>
          <view class="file-info">
            <view class="file-name">{{ selectedFile.name || '尚未选择文件' }}</view>
            <view class="muted">{{ selectedFile.path ? formatSize(selectedFile.size) : '仅支持 .xlsx' }}</view>
          </view>
          <text v-if="selectedFile.path && !busy" class="clear-link" @click="resetFile">清除</text>
        </view>
        <button class="primary-btn outline-btn" :disabled="busy || !canImport" @click="selectFile">
          选择 Excel
        </button>
      </view>

      <view class="card">
        <view class="card-title">3. 上传并导入</view>
        <view class="tips">
          系统会先校验整份文件。待新增行只要有一条错误，本批次就不会写入；已存在的商品编号会安全跳过。
        </view>
        <button
          class="primary-btn"
          :disabled="!selectedFile.path || busy || !canImport"
          @click="submitImport"
        >
          {{ uploading ? '正在校验并导入…' : '上传并导入商品' }}
        </button>
      </view>

      <view v-if="result" class="card result-card">
        <view class="result-head">
          <view class="card-title">导入结果</view>
          <text class="clear-link" @click="clearResult">清除结果</text>
        </view>
        <view class="summary-grid">
          <view class="summary-item">
            <text class="summary-number">{{ result.total_rows || 0 }}</text>
            <text class="summary-label">总行数</text>
          </view>
          <view class="summary-item success">
            <text class="summary-number">{{ result.created_count || 0 }}</text>
            <text class="summary-label">新增</text>
          </view>
          <view class="summary-item warning">
            <text class="summary-number">{{ result.skipped_count || 0 }}</text>
            <text class="summary-label">跳过</text>
          </view>
          <view class="summary-item danger">
            <text class="summary-number">{{ result.error_count || 0 }}</text>
            <text class="summary-label">错误</text>
          </view>
        </view>

        <view v-if="result.created?.length" class="result-section">
          <view class="section-heading success-text">新增成功</view>
          <view v-for="item in result.created" :key="`created-${item.row}`" class="result-line ok-line">
            第 {{ item.row }} 行：{{ item.code }} · {{ item.name }}
          </view>
        </view>

        <view v-if="result.skipped?.length" class="result-section">
          <view class="section-heading warning-text">已跳过</view>
          <view v-for="item in result.skipped" :key="`skipped-${item.row}`" class="result-line warning-line">
            第 {{ item.row }} 行（{{ item.code || '未识别编码' }}）：{{ item.reason }}
          </view>
        </view>

        <view v-if="result.errors?.length" class="result-section">
          <view class="section-heading danger-text">请修改后重新导入</view>
          <view v-for="(item, index) in result.errors" :key="`error-${item.row}-${index}`" class="result-line error-line">
            第 {{ item.row }} 行 · {{ item.field }}：{{ item.message }}
          </view>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { chooseExcelFile } from '@/utils/excelFilePicker'

const MAX_FILE_SIZE = 5 * 1024 * 1024
const auth = useAuth()
const downloading = ref(false)
const uploading = ref(false)
const result = ref(null)
const selectedFile = reactive({ path: '', name: '', size: 0 })

const profileReady = computed(() => auth.profileLoaded)
const canImport = computed(() => auth.canImportProducts)
const busy = computed(() => downloading.value || uploading.value)

onShow(() => {
  auth.loadProfile({ force: true }).catch((error) => {
    console.warn('商品导入权限资料加载失败', error)
  })
})

function formatSize(size) {
  const bytes = Number(size || 0)
  if (!bytes) return '大小未知'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function resetFile() {
  selectedFile.path = ''
  selectedFile.name = ''
  selectedFile.size = 0
}

function clearResult() {
  result.value = null
}

async function downloadTemplate() {
  if (!canImport.value || busy.value) return
  downloading.value = true
  try {
    const downloaded = await api.downloadProductImportTemplate()
    if (downloaded?.tempFilePath && typeof uni.openDocument === 'function') {
      uni.openDocument({
        filePath: downloaded.tempFilePath,
        fileType: 'xlsx',
        showMenu: true,
        fail: () => uni.showToast({ title: '模板已下载，请到下载目录查看', icon: 'none' }),
      })
    } else {
      uni.showToast({ title: '模板下载完成', icon: 'none' })
    }
  } catch (error) {
    console.error('download product template failed', error)
    uni.showToast({ title: error?.message || '模板下载失败', icon: 'none' })
  } finally {
    downloading.value = false
  }
}

async function selectFile() {
  if (busy.value) return
  try {
    const file = await chooseExcelFile()
    const name = file?.name || ''
    if (!name.toLowerCase().endsWith('.xlsx')) {
      uni.showToast({ title: '请选择 .xlsx 文件', icon: 'none' })
      return
    }
    if (file.size && file.size > MAX_FILE_SIZE) {
      uni.showToast({ title: '文件不能超过 5 MB', icon: 'none' })
      return
    }
    selectedFile.path = file.path
    selectedFile.name = name
    selectedFile.size = Number(file.size || 0)
    result.value = null
  } catch (error) {
    const message = String(error?.message || error?.errMsg || '')
    if (!message.includes('取消') && !message.includes('cancel')) {
      uni.showToast({ title: message || '选择文件失败', icon: 'none' })
    }
  }
}

async function submitImport() {
  if (!selectedFile.path || busy.value || !canImport.value) return
  uploading.value = true
  result.value = null
  try {
    result.value = await api.importProductsExcel(selectedFile.path)
    uni.showToast({
      title: `新增 ${result.value?.created_count || 0} 条，跳过 ${result.value?.skipped_count || 0} 条`,
      icon: 'none',
      duration: 2200,
    })
    resetFile()
  } catch (error) {
    console.error('product import failed', error)
    if (error?.data && Array.isArray(error.data.errors)) {
      result.value = error.data
    }
    uni.showToast({ title: error?.message || '商品导入失败', icon: 'none' })
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.product-import-page {
  min-height: 100vh;
  box-sizing: border-box;
  padding: 22rpx;
  background: #f3f4f6;
}
.card {
  margin: 0 0 20rpx;
  padding: 26rpx;
  border: 1rpx solid #e5e7eb;
  border-radius: 18rpx;
  background: #fff;
  box-shadow: 0 4rpx 16rpx rgba(15, 23, 42, .04);
}
.card-title {
  margin-bottom: 14rpx;
  color: #111827;
  font-size: 32rpx;
  font-weight: 700;
}
.muted {
  color: #6b7280;
  font-size: 24rpx;
  line-height: 1.65;
}
.primary-btn {
  height: 82rpx;
  margin-top: 22rpx;
  border: 0;
  border-radius: 12rpx;
  background: #2563eb;
  color: #fff;
  font-size: 28rpx;
  line-height: 82rpx;
}
.primary-btn[disabled] {
  background: #cbd5e1;
  color: #64748b;
}
.outline-btn {
  border: 1rpx solid #2563eb;
  background: #fff;
  color: #2563eb;
}
.file-box {
  display: flex;
  align-items: center;
  min-height: 100rpx;
  margin-top: 18rpx;
  padding: 18rpx;
  border: 2rpx dashed #cbd5e1;
  border-radius: 14rpx;
  background: #f8fafc;
}
.file-box.selected {
  border-style: solid;
  border-color: #93c5fd;
  background: #eff6ff;
}
.file-icon { margin-right: 16rpx; font-size: 42rpx; }
.file-info { min-width: 0; flex: 1; }
.file-name {
  overflow: hidden;
  color: #1f2937;
  font-size: 27rpx;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.clear-link { padding: 8rpx; color: #2563eb; font-size: 24rpx; }
.tips {
  padding: 16rpx;
  border-radius: 12rpx;
  background: #fffbeb;
  color: #92400e;
  font-size: 24rpx;
  line-height: 1.65;
}
.denied-card { margin-top: 20rpx; border-color: #fecaca; background: #fef2f2; }
.result-head { display: flex; align-items: center; justify-content: space-between; }
.summary-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12rpx; }
.summary-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 18rpx 8rpx;
  border-radius: 12rpx;
  background: #f1f5f9;
}
.summary-item.success { background: #ecfdf5; }
.summary-item.warning { background: #fffbeb; }
.summary-item.danger { background: #fef2f2; }
.summary-number { color: #0f172a; font-size: 34rpx; font-weight: 700; }
.summary-label { margin-top: 4rpx; color: #64748b; font-size: 22rpx; }
.result-section { margin-top: 24rpx; }
.section-heading { margin-bottom: 10rpx; font-size: 27rpx; font-weight: 700; }
.success-text { color: #047857; }
.warning-text { color: #a16207; }
.danger-text { color: #b91c1c; }
.result-line {
  margin-bottom: 10rpx;
  padding: 14rpx;
  border-radius: 10rpx;
  font-size: 24rpx;
  line-height: 1.55;
  word-break: break-all;
}
.ok-line { background: #ecfdf5; color: #065f46; }
.warning-line { background: #fffbeb; color: #92400e; }
.error-line { background: #fef2f2; color: #991b1b; }
</style>

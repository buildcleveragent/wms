<template>
  <view class="page">
    <view class="owner-card">
      <view class="owner-label">当前货主</view>
      <view class="owner-name">{{ cart.owner?.name || '-' }}</view>
    </view>

    <view class="step-card">
      <view class="step-title">1. 下载标准模板</view>
      <view class="tip">模板按当前货主生成，包含可用商品、单位及换算参考。</view>
      <button class="outline-btn" :disabled="busy" @click="downloadTemplate">
        {{ downloading ? '模板下载中…' : '下载 Excel 模板' }}
      </button>
    </view>

    <view class="step-card">
      <view class="step-title">2. 选择填写好的文件</view>
      <view class="file-box" :class="{ selected: selectedFile.path }">
        <view class="file-icon">📄</view>
        <view class="file-info">
          <view class="file-name">{{ selectedFile.name || '尚未选择文件' }}</view>
          <view class="tip">{{ selectedFile.path ? formatSize(selectedFile.size) : '仅支持 .xlsx，最大 5 MB' }}</view>
        </view>
        <text v-if="selectedFile.path && !busy" class="link" @click="clearSelection">清除</text>
      </view>
      <button class="outline-btn" :disabled="busy || !!successResult" @click="selectFile">
        {{ selecting ? '正在打开文件…' : '选择 Excel' }}
      </button>
    </view>

    <view class="step-card">
      <view class="step-title">3. 上传并校验</view>
      <view class="tip">系统只做校验和预览，此步骤不会增加库存。任意一行错误时整批不能确认。</view>
      <button class="primary-btn" :disabled="!selectedFile.path || busy || !!successResult" @click="uploadPreview">
        {{ previewing ? '正在校验…' : '上传校验并预览' }}
      </button>
    </view>

    <view v-if="validationResult" class="step-card">
      <view class="summary-head">
        <view class="step-title">校验结果</view>
        <text :class="validationResult.error_count ? 'error-text' : 'success-text'">
          {{ validationResult.error_count ? `${validationResult.error_count} 个错误` : '全部通过' }}
        </text>
      </view>
      <view class="summary-grid">
        <view class="summary-item"><text class="summary-number">{{ validationResult.total_rows || 0 }}</text><text>明细行</text></view>
        <view class="summary-item"><text class="summary-number">{{ validationResult.product_count || 0 }}</text><text>商品</text></view>
      </view>

      <view v-if="validationResult.errors?.length" class="error-list">
        <view v-for="(error, index) in validationResult.errors" :key="`${error.row}-${error.field}-${index}`" class="error-row">
          第 {{ error.row }} 行 · {{ error.field }}：{{ error.message }}
        </view>
      </view>

      <view v-if="validationResult.rows?.length" class="preview-list">
        <view v-for="row in validationResult.rows" :key="row.row" class="preview-row">
          <view class="preview-head">
            <text>第 {{ row.row }} 行 · {{ row.product_code }}</text>
            <text class="base-qty">{{ row.base_qty }} {{ row.base_uom_code }}</text>
          </view>
          <view class="product-name">{{ row.product_name }}</view>
          <view class="preview-meta">{{ row.input_qty }} {{ row.uom_code }} × {{ row.multiplier }}</view>
          <view v-if="row.lot_no || row.mfg_date || row.exp_date" class="preview-meta">
            批次 {{ row.lot_no || '-' }} · 生产 {{ row.mfg_date || '-' }} · 有效 {{ row.exp_date || '-' }}
          </view>
        </view>
      </view>
    </view>

    <view v-if="canConfirm && !successResult" class="step-card confirm-card">
      <view class="step-title">4. 确认批量入库</view>
      <view class="warning">确认后将立即生成无订单收货任务并增加库存，请再次核对明细。</view>
      <button class="primary-btn confirm-btn" :disabled="busy" @click="confirmImport">
        {{ confirming ? '正在入库，请勿重复操作…' : '确认并批量入库' }}
      </button>
    </view>

    <view v-if="successResult" class="step-card success-card">
      <view class="success-title">批量入库成功</view>
      <view class="task-no">任务号：{{ successResult.task_no || successResult.task_id }}</view>
      <view class="action-row">
        <button class="outline-btn small-btn" @click="printTask">查看/打印收货单</button>
        <button class="outline-btn small-btn" @click="exportTask">导出收货单</button>
      </view>
      <button class="primary-btn" @click="startAnother">继续导入下一批</button>
      <button class="plain-btn" @click="goHome">返回首页</button>
    </view>
  </view>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { chooseExcelFile } from '@/utils/excelFilePicker'
import { exportReceiveTaskExcel, openReceiveTaskPrint } from '@/utils/receiveTaskDocument'
import { useCart } from '@/store/cart'

const MAX_FILE_SIZE = 5 * 1024 * 1024
const cart = useCart()
const downloading = ref(false)
const selecting = ref(false)
const previewing = ref(false)
const confirming = ref(false)
const validationResult = ref(null)
const successResult = ref(null)
const selectedFile = reactive({ path: '', name: '', size: 0 })

const busy = computed(() => downloading.value || selecting.value || previewing.value || confirming.value)
const canConfirm = computed(() =>
  validationResult.value?.can_confirm === true &&
  validationResult.value?.preview_token &&
  validationResult.value?.request_id &&
  Array.isArray(validationResult.value?.items)
)

onLoad(() => {
  if (!cart.owner?.id) {
    uni.redirectTo({ url: '/pages/inbound/createwithoutorder/selectowner' })
  }
})

function formatSize(size) {
  const bytes = Number(size || 0)
  if (!bytes) return '大小未知'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function clearSelection() {
  selectedFile.path = ''
  selectedFile.name = ''
  selectedFile.size = 0
  validationResult.value = null
}

async function downloadTemplate() {
  if (!cart.owner?.id || busy.value) return
  downloading.value = true
  try {
    const downloaded = await api.downloadNoOrderReceiveImportTemplate(cart.owner.id)
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
    uni.showToast({ title: error?.message || '模板下载失败', icon: 'none' })
  } finally {
    downloading.value = false
  }
}

async function selectFile() {
  if (busy.value) return
  selecting.value = true
  try {
    const file = await chooseExcelFile({
      fallbackName: '无订单批量入库.xlsx',
      cachePrefix: 'inbound-receive-import',
    })
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
    validationResult.value = null
  } catch (error) {
    const message = String(error?.message || error?.errMsg || '')
    if (!message.includes('取消') && !message.includes('cancel')) {
      uni.showToast({ title: message || '选择文件失败', icon: 'none' })
    }
  } finally {
    selecting.value = false
  }
}

async function uploadPreview() {
  if (!selectedFile.path || !cart.owner?.id || busy.value) return
  previewing.value = true
  validationResult.value = null
  try {
    validationResult.value = await api.previewNoOrderReceiveImport(selectedFile.path, cart.owner.id)
    uni.showToast({ title: '校验通过，请核对后确认入库', icon: 'none' })
  } catch (error) {
    if (error?.data && (Array.isArray(error.data.errors) || error.data.detail)) {
      validationResult.value = {
        total_rows: error.data.total_rows || 0,
        product_count: error.data.product_count || 0,
        error_count: error.data.error_count || (error.data.detail ? 1 : 0),
        rows: error.data.rows || [],
        errors: error.data.errors || [{ row: '-', field: '文件', message: error.data.detail }],
      }
    }
    uni.showToast({ title: error?.message || 'Excel 校验失败', icon: 'none' })
  } finally {
    previewing.value = false
  }
}

async function confirmImport() {
  if (!canConfirm.value || busy.value) return
  confirming.value = true
  try {
    successResult.value = await api.confirmNoOrderReceiveImport({
      preview_token: validationResult.value.preview_token,
      request_id: validationResult.value.request_id,
      items: validationResult.value.items,
    })
    uni.showToast({ title: `入库成功：${successResult.value.task_no || successResult.value.task_id}`, icon: 'none' })
  } catch (error) {
    uni.showToast({ title: error?.message || '批量入库失败', icon: 'none' })
  } finally {
    confirming.value = false
  }
}

function printTask() {
  if (successResult.value?.task_id) openReceiveTaskPrint(successResult.value.task_id)
}

async function exportTask() {
  if (!successResult.value?.task_id) return
  try {
    await exportReceiveTaskExcel(successResult.value.task_id)
  } catch (error) {
    uni.showToast({ title: error?.message || '收货单导出失败', icon: 'none' })
  }
}

function startAnother() {
  successResult.value = null
  clearSelection()
}

function goHome() {
  uni.switchTab({ url: '/pages/index/index' })
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 22rpx; background: #f3f4f6; }
.owner-card, .step-card { background: #fff; border-radius: 16rpx; padding: 24rpx; margin-bottom: 20rpx; box-shadow: 0 2rpx 10rpx rgba(15, 23, 42, .05); }
.owner-card { display: flex; justify-content: space-between; align-items: center; }
.owner-label, .tip, .preview-meta { color: #64748b; font-size: 25rpx; }
.owner-name, .step-title { color: #0f172a; font-size: 31rpx; font-weight: 700; }
.step-title { margin-bottom: 12rpx; }
.tip { line-height: 1.6; margin-bottom: 18rpx; }
.primary-btn, .outline-btn, .plain-btn { width: 100%; margin-top: 16rpx; border-radius: 12rpx; font-size: 28rpx; }
.primary-btn { color: #fff; background: #2563eb; }
.outline-btn { color: #2563eb; background: #fff; border: 2rpx solid #2563eb; }
.plain-btn { color: #475569; background: #e2e8f0; }
.file-box { display: flex; align-items: center; gap: 18rpx; padding: 20rpx; border: 2rpx dashed #cbd5e1; border-radius: 12rpx; }
.file-box.selected { border-color: #2563eb; background: #eff6ff; }
.file-icon { font-size: 42rpx; }
.file-info { flex: 1; min-width: 0; }
.file-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
.file-info .tip { margin: 6rpx 0 0; }
.link { color: #2563eb; font-size: 26rpx; }
.summary-head, .preview-head, .action-row { display: flex; justify-content: space-between; align-items: center; }
.success-text { color: #15803d; font-weight: 700; }
.error-text { color: #dc2626; font-weight: 700; }
.summary-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16rpx; margin: 16rpx 0; }
.summary-item { display: flex; flex-direction: column; align-items: center; padding: 18rpx; background: #f8fafc; border-radius: 12rpx; color: #64748b; }
.summary-number { color: #0f172a; font-size: 38rpx; font-weight: 700; }
.error-list { margin-top: 16rpx; }
.error-row { padding: 16rpx; margin-bottom: 10rpx; border-radius: 10rpx; color: #b91c1c; background: #fef2f2; font-size: 25rpx; line-height: 1.5; }
.preview-list { max-height: 760rpx; overflow-y: auto; margin-top: 18rpx; }
.preview-row { padding: 18rpx 0; border-bottom: 1rpx solid #e2e8f0; }
.preview-head { font-weight: 700; }
.base-qty { color: #2563eb; }
.product-name { margin: 8rpx 0; }
.preview-meta { line-height: 1.5; }
.confirm-card { border: 2rpx solid #f59e0b; }
.warning { color: #92400e; background: #fffbeb; padding: 18rpx; border-radius: 10rpx; line-height: 1.6; }
.confirm-btn { background: #d97706; }
.success-card { border: 2rpx solid #22c55e; text-align: center; }
.success-title { color: #15803d; font-size: 36rpx; font-weight: 700; }
.task-no { margin: 18rpx 0; font-size: 30rpx; }
.action-row { gap: 14rpx; }
.small-btn { flex: 1; font-size: 24rpx; padding: 0 8rpx; }
button[disabled] { opacity: .55; }
</style>

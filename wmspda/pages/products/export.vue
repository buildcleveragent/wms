<template>
  <view class="page product-export-page">
    <view v-if="profileReady && !canExport" class="card denied-card">
      <view class="card-title">暂无商品档案导出权限</view>
      <view class="muted">请联系管理员授予商品查看权限，并配置有效的货主数据范围。</view>
    </view>

    <template v-else>
      <view class="card">
        <view class="card-title">选择货主</view>
        <view class="muted">商品档案按单个货主导出；必填字段会排列在 Excel 最前面。</view>
        <view class="search-row">
          <input
            v-model="keyword"
            class="search-input"
            placeholder="输入货主编码或名称"
            :disabled="busy"
            @confirm="searchOwners"
          />
          <button class="search-btn" size="mini" :disabled="busy" @click="searchOwners">
            查询
          </button>
        </view>

        <view v-if="loadingOwners" class="empty-state">正在加载货主…</view>
        <view v-else-if="owners.length === 0" class="empty-state">没有可导出的货主</view>
        <view v-else class="owner-list">
          <view
            v-for="owner in owners"
            :key="owner.id"
            class="owner-item"
            :class="{ selected: selectedOwner?.id === owner.id }"
            @click="selectOwner(owner)"
          >
            <view>
              <view class="owner-name">{{ owner.name }}</view>
              <view class="muted">{{ owner.code }}</view>
            </view>
            <text class="radio">{{ selectedOwner?.id === owner.id ? '●' : '○' }}</text>
          </view>
          <button
            v-if="nextPage"
            class="more-btn"
            :disabled="busy"
            @click="loadMore"
          >
            加载更多
          </button>
        </view>
      </view>

      <view class="card">
        <view class="card-title">导出 Excel</view>
        <view class="tips">
          文件包含“商品导入”和“商品包装”两个工作表，可用于重新导入。商品超过 1000 条时，回导前需拆分文件。
        </view>
        <button
          class="primary-btn"
          :disabled="!selectedOwner || busy || !canExport"
          @click="exportArchive"
        >
          {{ exporting ? '正在生成并下载…' : '导出商品档案' }}
        </button>
      </view>
    </template>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'

const auth = useAuth()
const owners = ref([])
const selectedOwner = ref(null)
const keyword = ref('')
const nextPage = ref(null)
const loadingOwners = ref(false)
const exporting = ref(false)
const profileReady = computed(() => auth.profileLoaded)
const canExport = computed(() => auth.canExportProducts)
const busy = computed(() => loadingOwners.value || exporting.value)

async function refreshProfile() {
  try {
    await auth.loadProfile({ force: true })
    if (auth.canExportProducts && owners.value.length === 0) await fetchOwners(1)
  } catch (error) {
    console.warn('商品导出权限资料加载失败', error)
  }
}

async function fetchOwners(page = 1, append = false) {
  if (!auth.canExportProducts) return
  loadingOwners.value = true
  try {
    const result = await api.productExportOwners(keyword.value.trim(), page)
    const rows = Array.isArray(result?.results) ? result.results : []
    owners.value = append ? [...owners.value, ...rows] : rows
    nextPage.value = result?.next || null
    if (!append) {
      const stillVisible = rows.find((owner) => owner.id === selectedOwner.value?.id)
      selectedOwner.value = stillVisible || null
      if (Number(result?.count) === 1 && rows.length === 1) selectedOwner.value = rows[0]
    }
  } catch (error) {
    owners.value = append ? owners.value : []
    uni.showToast({ title: error?.message || '货主加载失败', icon: 'none' })
  } finally {
    loadingOwners.value = false
  }
}

function searchOwners() {
  if (busy.value) return
  fetchOwners(1)
}

function loadMore() {
  if (nextPage.value && !busy.value) fetchOwners(nextPage.value, true)
}

function selectOwner(owner) {
  if (!busy.value) selectedOwner.value = owner
}

async function exportArchive() {
  if (!selectedOwner.value || busy.value) return
  exporting.value = true
  try {
    const result = await api.downloadProductArchive(
      selectedOwner.value.id,
      selectedOwner.value.code,
    )
    if (result?.tempFilePath) {
      // #ifndef H5
      const filePath = await new Promise((resolve) => {
        uni.saveFile({
          tempFilePath: result.tempFilePath,
          success: (saved) => resolve(saved.savedFilePath || result.tempFilePath),
          fail: () => resolve(result.tempFilePath),
        })
      })
      uni.openDocument({
        filePath,
        fileType: 'xlsx',
        showMenu: true,
        fail: () => uni.showToast({ title: '文件已下载，请在文件管理中查看', icon: 'none' }),
      })
      // #endif
    }
    uni.showToast({ title: '商品档案已导出', icon: 'success' })
  } catch (error) {
    if (Number(error?.statusCode) === 403) {
      await auth.loadProfile({ force: true }).catch(() => {})
    }
    uni.showToast({ title: error?.message || '商品档案导出失败', icon: 'none' })
  } finally {
    exporting.value = false
  }
}

onShow(refreshProfile)
</script>

<style scoped>
.page { padding: 24rpx; }
.card { margin-bottom: 24rpx; padding: 28rpx; background: #fff; border-radius: 20rpx; box-shadow: 0 6rpx 24rpx rgba(0, 0, 0, .05); }
.card-title { margin-bottom: 12rpx; font-size: 32rpx; font-weight: 700; }
.muted { color: #6b7280; font-size: 25rpx; }
.search-row { display: flex; gap: 16rpx; margin-top: 24rpx; }
.search-input { flex: 1; height: 72rpx; padding: 0 20rpx; background: #f3f4f6; border-radius: 12rpx; }
.search-btn { width: 140rpx; margin: 0; }
.owner-list { margin-top: 20rpx; }
.owner-item { display: flex; align-items: center; justify-content: space-between; padding: 20rpx; border: 2rpx solid #e5e7eb; border-radius: 14rpx; margin-bottom: 14rpx; }
.owner-item.selected { border-color: #2563eb; background: #eff6ff; }
.owner-name { margin-bottom: 6rpx; font-size: 29rpx; font-weight: 600; }
.radio { color: #2563eb; font-size: 36rpx; }
.empty-state { padding: 36rpx 0; color: #6b7280; text-align: center; }
.tips { margin: 14rpx 0 24rpx; padding: 18rpx; color: #475569; background: #f8fafc; border-radius: 12rpx; font-size: 25rpx; line-height: 1.6; }
.primary-btn { color: #fff; background: #2563eb; border-radius: 14rpx; }
.primary-btn[disabled] { opacity: .45; }
.more-btn { margin-top: 12rpx; color: #2563eb; background: #eff6ff; }
.denied-card { border-left: 8rpx solid #dc2626; }
</style>

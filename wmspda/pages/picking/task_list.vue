<template>
  <view class="page">
    <view class="header">拣货任务列表</view>

    <view v-if="loading" class="hint">加载中...</view>
    <view v-else-if="rows.length === 0" class="hint">暂无可拣货任务</view>

    <view v-else class="list">
      <!-- <view v-for="t in rows" :key="t.id" class="card" @click="openTask(t)">
        <view class="row-first">
          <text class="task-no">任务号：{{ t.task_no }}</text>
          <text class="status">{{ t.status }}</text>
        </view>
        <view class="row-second">
          <text>货主：{{ t.owner_name }}</text>
        </view>
        <view class="row-second">
          <text>仓库：{{ t.warehouse_name }}</text>
        </view>
      </view> -->
	  
	  <view v-for="t in rows" :key="t.id" class="card" @click="openTask(t)">
	    <view class="row-first">
	      <text class="task-no">任务号：{{ t.task_no }}</text>
	      <view class="right">
	        <text class="status">{{ t.status }}</text>
	        <button class="btn-print" @click.stop="printPickList(t)">打印清单</button>
	      </view>
	    </view>
	  
	    <view class="row-second">
	      <text>货主：{{ t.owner_name }}</text>
	    </view>
	    <view class="row-second">
	      <text>仓库：{{ t.warehouse_name }}</text>
	    </view>
	  </view>

    </view>
  </view>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad,onShow} from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { BASE_URL } from '@/utils/request'


const loading = ref(false)
const rows = ref<any[]>([])

async function fetchTasks() {
  loading.value = true
  try {
    console.log("pickTasks")
	const res: any = await api.pickTasks()
	
    // DRF list: { count, next, previous, results } 或直接数组
    rows.value = Array.isArray(res) ? res : (res.results || [])
	console.log("pickTasks rows.value",rows.value)
  } catch (e) {
    console.error(e)
    uni.showToast({ title: '加载拣货任务失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function openTask(t: any) {
  uni.navigateTo({ url: `/pages/picking/task_detail?task_id=${t.id}` })
}

function printPickList(t: any) {
  const token = uni.getStorageSync('access') || ''
  if (!token) {
    uni.showToast({ title: '未登录或缺少token', icon: 'none' })
    return
  }

  // 你后端新增的打印接口：/api/pda/pick-tasks/<id>/print/?token=...
  // 这里需要 BASE_URL。最简单：在 utils/request.js 里把 BASE_URL export 出来（下面有后端配套说明）
  const url = `${BASE_URL}/api/pda/pick-tasks/${t.id}/print/?token=${encodeURIComponent(token)}`

  // #ifdef H5
  window.open(url)
  // #endif

  // #ifdef APP-PLUS
  plus.runtime.openURL(url)
  // #endif
}


onShow(() => {
  fetchTasks()
})

</script>

<style scoped>
.page {
  padding: 16rpx;
}
.header {
  font-size: 32rpx;
  font-weight: 600;
  margin-bottom: 16rpx;
}
.hint {
  margin-top: 40rpx;
  text-align: center;
  color: #64748b;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.card {
  background: #fff;
  border-radius: 16rpx;
  padding: 16rpx;
  box-shadow: 0 2rpx 6rpx rgba(15, 23, 42, 0.08);
}
.row-first {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8rpx;
}
.row-second {
  font-size: 24rpx;
  color: #475569;
}
.task-no {
  font-weight: 600;
}
.status {
  font-size: 24rpx;
  color: #0f766e;
}

.right {
  display: flex;
  align-items: center;
  gap: 12rpx;
}

.btn-print {
  padding: 6rpx 12rpx;
  border-radius: 8rpx;
  border: 1rpx solid #cbd5e1;
  background: #fff;
  font-size: 22rpx;
  line-height: 1.2;
}

</style>

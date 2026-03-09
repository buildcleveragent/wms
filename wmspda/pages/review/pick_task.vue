<template>
  <view class="page">
    <!-- 顶部：头信息 -->
    <view class="header">
      <view>任务号：{{ task.task_no }}</view>
      <view>单据号：{{ task.ref_no }}</view>
      <view>拣货人：{{ task.executor_name || '-' }}</view>
      <view>状态：{{ headerStatusText }}</view>
    </view>

    <!-- 中部：行列表 -->
    <scroll-view scroll-y class="lines">
      <view v-for="line in lines" :key="line.id"
            class="line-item"
            :class="{ abnormal: isAbnormal(line) }">
        <view class="line-top">
          <text class="loc">[{{ line.from_location_code }}]</text>
          <text class="name">{{ line.product_name }}</text>
        </view>
        <view class="line-mid">
          <text class="code">编码：{{ line.product_code }}</text>
        </view>
        <view class="line-bottom">
          <text>计划：{{ line.qty_plan }} {{ line.uom }}</text>
          <text class="right">实拣：{{ line.qty_done }} {{ line.uom }}</text>
        </view>
        <view v-if="isAbnormal(line)" class="badge">
          {{ abnormalText(line) }}
        </view>
      </view>
    </scroll-view>

    <!-- 底部：合计 + 按钮 -->
    <view class="footer">
      <view class="summary">
        行 {{ totalLines }}  
        计划 {{ totalPlanQty }}  
        实拣 {{ totalDoneQty }}  
        异常 {{ abnormalCount }}
      </view>
      <view class="actions">
        <!-- 有驳回需求再打开 -->
        <!-- <button class="btn-secondary" @click="rejectReview">驳回</button> -->
        <button class="btn-primary" :disabled="posting" @click="approveAndPost">
          复核通过
        </button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, computed } from 'vue'
import { onLoad } from '@dcloudio/uni-app' 
import { api } from '@/utils/request' // 你自己的封装

const taskId = ref(null)
const task = ref({})
const lines = ref([])
const posting = ref(false)

const totalLines = computed(() => lines.value.length)
const totalPlanQty = computed(() => lines.value.reduce((s, l) => s + Number(l.qty_plan || 0), 0))
const totalDoneQty = computed(() => lines.value.reduce((s, l) => s + Number(l.qty_done || 0), 0))
const abnormalCount = computed(() => lines.value.filter(isAbnormal).length)

const headerStatusText = computed(() => {
  if (!task.value.status) return ''
  if (task.value.status === 'COMPLETED' && task.value.review_status === 'PENDING') {
    return '已拣完，待复核'
  }
  return `${task.value.status} / ${task.value.review_status}`
})

function isAbnormal(line) {
  // return Number(line.qty_plan) !== Number(line.qty_done) || line.status !== 'COMPLETED'
    return Number(line.qty_plan) !== Number(line.qty_done) 
}

function abnormalText(line) {
  if (Number(line.qty_done) < Number(line.qty_plan)) return '数量不足'
  if (Number(line.qty_done) > Number(line.qty_plan)) return '数量超出'
  if (line.status !== 'COMPLETED') return `状态：${line.status}`
  return '异常'
}

async function loadData(id) {
  const [taskRes, lineRes] = await Promise.all([
    api.pickTaskDetail(id),
    api.pickTaskLines(id),
  ])
  task.value = taskRes
  lines.value = lineRes || []
  console.log("abc")
  
  console.log("task.value.status",task.value.status)
  console.log("task.value.review_status",task.value.review_status)
  uni.showToast({ title:task.value.status, icon: 'none' })

  if (!(task.value.status === 'COMPLETED' && task.value.review_status === 'PENDING')) {
    uni.showToast({ title: '任务不在待复核状态', icon: 'none' })
    setTimeout(() => uni.navigateBack(), 800)
  }
}

// async function approveAndPost() {
//   if (!taskId.value) return
//   if (abnormalCount.value > 0) {
//     const [err, res] = await uni.showModal({
//       title: '存在异常行',
//       content: `共有 ${abnormalCount.value} 行数量异常，仍然要复核通过并过账吗？`,
//     })
//     if (err || !res.confirm) return
//   }
//   posting.value = true
//   try {
//     const res = await api.postPickTask(taskId.value)
//     uni.showToast({ title: res.message || '复核通过，已过账', icon: 'none' })
//     setTimeout(() => uni.navigateBack(), 800)
//   } catch (e) {
//     const msg = e?.data?.detail || e?.data?.message || '复核/过账失败'
//     uni.showToast({ title: String(msg), icon: 'none' })
//   } finally {
//     posting.value = false
//   }
// }



async function approveAndPost() {
  if (!taskId.value) return

  if (abnormalCount.value > 0) {
    uni.showModal({
      title: '存在异常行',
      content: `共有 ${abnormalCount.value} 行数量异常，仍然要复核通过并过账吗？`,
      success: async (res) => {
        if (!res.confirm) return
        await doPost()
      },
    })
  } else {
    await doPost()
  }
}

async function doPost() {
  if (!taskId.value) return
  posting.value = true
  try {
    const res = await api.postPickTask(taskId.value)
    uni.showToast({
      title: res?.message || '复核通过，已过账',
      icon: 'none',
    })
    setTimeout(() => {
      uni.navigateBack()
    }, 800)
  } catch (e) {
    const msg = e?.data?.detail || e?.data?.message || '复核/过账失败'
    uni.showToast({ title: String(msg), icon: 'none' })
  } finally {
    posting.value = false
  }
}



onLoad((opts) => {
  taskId.value = Number(opts.task_id)
  loadData(taskId.value)
})
</script>

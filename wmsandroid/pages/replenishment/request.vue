<template>
  <view class="page">
    <view v-if="canRequest" class="card">
      <text class="title">提交要货申请</text>
      <picker :range="policyLabels" @change="selectPolicy">
        <view class="picker">{{ selectedLabel || '选择商品与目标拣货位' }}</view>
      </picker>
      <input v-model="qty" class="input" type="digit" placeholder="申请数量（基本单位）" />
      <input v-model="reason" class="input" placeholder="申请原因" />
      <button type="primary" @click="submit">提交申请</button>
    </view>

    <text class="section">申请记录</text>
    <view v-for="item in requests" :key="item.id" class="card">
      <text class="product">{{ item.product_name }} → {{ item.target_location_code }}</text>
      <text class="meta">数量 {{ item.requested_qty }} · {{ item.status }}</text>
      <text class="meta">原因：{{ item.reason }}</text>
      <view v-if="canApprove && item.status === 'PENDING'" class="actions">
        <button size="mini" type="primary" @click="approve(item)">批准</button>
        <button size="mini" @click="reject(item)">驳回</button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'

const auth = useAuth()
const policies = ref([])
const requests = ref([])
const selected = ref(null)
const qty = ref('')
const reason = ref('')
const policyLabels = computed(() => policies.value.map((p) => `${p.product_name} → ${p.target_location_code}`))
const selectedLabel = computed(() => selected.value ? `${selected.value.product_name} → ${selected.value.target_location_code}` : '')
const canRequest = computed(() => auth.canRequestReplenishment)
const canApprove = computed(() => auth.canApproveReplenishment)

function selectPolicy(event) { selected.value = policies.value[Number(event.detail.value)] }
async function load() {
  const [policyData, requestData] = await Promise.all([api.replenishmentPolicies(), api.replenishmentRequests()])
  policies.value = Array.isArray(policyData) ? policyData : (policyData?.results || [])
  requests.value = Array.isArray(requestData) ? requestData : (requestData?.results || [])
}
async function submit() {
  if (!selected.value || Number(qty.value) <= 0 || !reason.value.trim()) {
    uni.showToast({ title:'请选择策略并填写数量和原因', icon:'none' }); return
  }
  await api.createReplenishmentRequest({ policy_id:selected.value.id, requested_qty:Number(qty.value), reason:reason.value.trim() })
  qty.value = ''; reason.value = ''; await load()
}
async function approve(item) { await api.approveReplenishmentRequest(item.id); await load() }
async function reject(item) {
  uni.showModal({ title:'驳回申请', editable:true, placeholderText:'请输入驳回原因', success: async (res) => {
    if (res.confirm && res.content) { await api.rejectReplenishmentRequest(item.id, { note:res.content }); await load() }
  }})
}
onShow(async () => {
  await auth.loadProfile({ force:true })
  await load()
})
</script>

<style scoped>
.page { min-height:100vh; padding:24rpx; background:#f5f7fb; box-sizing:border-box; }
.card { margin-bottom:18rpx; padding:24rpx; background:#fff; border-radius:16rpx; }
.title,.section,.product { display:block; font-size:30rpx; font-weight:700; color:#172033; }
.section { margin:28rpx 0 16rpx; }
.picker,.input { height:72rpx; line-height:72rpx; margin-top:16rpx; padding:0 18rpx; border:1rpx solid #d7dfeb; border-radius:12rpx; }
.meta { display:block; margin-top:10rpx; color:#65758b; font-size:24rpx; }
.actions { display:flex; justify-content:flex-end; gap:14rpx; margin-top:16rpx; }
.actions button { margin:0; }
button { margin-top:18rpx; }
</style>

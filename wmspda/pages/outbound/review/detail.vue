<template>
  <view class="review-page">
    <view class="title">出库确认详情</view>
    <view v-if="denied" class="card">正在核验权限或当前账号无出库审核权限</view>
    <template v-else-if="order">
      <view class="card">
        <view class="row"><text class="strong">{{ order.order_no }}</text><text class="status">{{ reviewStatus(order.approval_status) }}</text></view>
        <view>货主：{{ order.owner_name }} · 仓库：{{ order.warehouse_name }}</view>
        <view>客户：{{ order.customer_name || '—' }}</view>
        <view>配送方式：{{ delivery(order.delivery_method) }}</view>
        <view>收件人：{{ order.contact || '—' }} {{ order.contact_phone }}</view>
        <view>地址：{{ order.ship_to || '—' }}</view>
        <view>备注：{{ order.memo || '—' }}</view>
        <view class="strong amount-summary">{{ amountSummaryLabel(order) }}</view>
      </view>
      <view v-if="order.approval_status === 'WHS_PENDING'" class="card warning">
        <view>等待补货，尚未发布拣货任务。补货完成后系统将尝试自动发布。</view>
        <view v-if="order.replenishment_wait?.task_nos?.length">补货任务：{{ order.replenishment_wait.task_nos.join('、') }}</view>
      </view>
      <view class="card">
        <view class="strong">商品与库存分配</view>
        <view v-for="line in order.lines" :key="line.id" class="product-line">
          <view class="strong">{{ line.product_name }} <text class="muted">{{ line.product_sku }}</text></view>
          <view>出库数量 {{ formatQuantity(line.base_qty) }} {{ line.base_unit_name }} · 已分配 {{ formatQuantity(line.allocated_qty) }} · 缺口 {{ formatQuantity(line.shortage_qty) }}</view>
          <view v-if="line.is_priced === false">未计价</view>
          <view v-else>单价 ¥{{ formatQuantity(line.base_price) }} · 金额 ¥{{ money(line.amount) }}</view>
        </view>
      </view>
      <view class="card">
        <view>仓库审核人：{{ order.approved_by_warehouse_name || '—' }}</view>
        <view>审核时间：{{ order.approved_at_warehouse || '—' }}</view>
        <view v-if="order.warehouse_reject_reason" class="warning">最近仓库退回原因：{{ order.warehouse_reject_reason }}</view>
        <view v-if="order.approval_status === 'WHS_REJECTED'">原业务员需撤回修改，再重新提交货主管理员审核。</view>
      </view>
      <view v-if="order.can_warehouse_confirm || order.can_warehouse_reject" class="card">
        <button v-if="order.can_warehouse_confirm" class="primary confirm-button" :disabled="!canAct" @click="act('confirm')">确认并发布</button>
        <template v-if="order.can_warehouse_reject">
          <textarea v-model="reason" maxlength="500" :disabled="busy" placeholder="退回原因（必填，最多 500 字）" />
          <button class="reject-button" :disabled="!canAct" @click="act('reject')">退回</button>
        </template>
      </view>
    </template>
    <view v-if="loading || busy" class="hint">{{ busy ? '正在处理…' : '正在加载…' }}</view>
    <view v-if="error" class="card warning">{{ error }}</view>
    <button :disabled="loading || busy" @click="load({ authorize: true })">刷新订单</button>
  </view>
</template>
<script setup>
import { ref } from 'vue'
import { onLoad, onShow, onUnload, onPullDownRefresh } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { useWarehouseReview, reviewStatus, formatQuantity, amountSummaryLabel } from '@/utils/warehouseReview'
const id = ref(0)
const session = useWarehouseReview({
  api, auth: useAuth(), id,
  confirm: content => new Promise(resolve => uni.showModal({ title: '出库确认', content, confirmText: '确定', cancelText: '取消', success: r => resolve(Boolean(r.confirm)), fail: () => resolve(false) })),
  notify: title => uni.showToast({ title, icon: 'none' }),
})
const { order, denied, loading, busy, error, reason, canAct, load, act } = session
onLoad(query => { id.value = Number(query.id) })
const delivery = value => ({ PICKUP: '客户自提', OWN_TRUCK: '配送', COURIER: '快递/小包' })[value] || value || '—'
const money = value => Number(value || 0).toFixed(2)
onShow(() => load({ authorize: true }))
onPullDownRefresh(async () => { await load({ authorize: true }); uni.stopPullDownRefresh() })
onUnload(session.dispose)
</script>
<style src="./review.css"></style>

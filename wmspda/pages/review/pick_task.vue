<template>
  <view class="page">
    <view class="header">拣货复核</view>
    <view v-if="denied" class="hint warning">当前账号无此仓库的复核权限</view>
    <template v-else-if="task">
      <view v-if="order" class="card order-info">
        <view class="task-no">订单信息：{{ order.order_no }}</view>
        <view>客户：{{ recorded(order.customer_name) }}</view>
        <view>货主：{{ order.owner_name }} · 仓库：{{ order.warehouse_name }}</view>
        <view>业务日期：{{ recorded(order.biz_date) }}</view>
        <view>配送方式：{{ recorded(order.delivery_method) }}</view>
        <view>收货地址：{{ recorded(order.ship_to) }}</view>
        <view>订单备注：{{ recorded(order.memo) }}</view>
      </view>
      <view class="card">
        <view class="task-no">复核任务：{{ task.review_task?.task_no || '关联异常' }}</view>
        <view>来源订单：{{ task.ref_no || '—' }}</view>
        <view>拣货任务：{{ task.task_no }}</view>
        <view>拣货人：{{ task.picked_by_name || '—' }}</view>
        <view>拣货完成时间：{{ displayTime(task.finished_at) }}</view>
        <view>货主：{{ task.owner_name }} · 仓库：{{ task.warehouse_name }}</view>
        <view class="status">{{ task.posting_status === 'POSTED' ? '已完成复核并过账' : task.can_retry_post ? '已复核，待重试过账' : '待复核' }}</view>
        <view v-if="task.blocked_reason" class="warning">{{ task.blocked_reason }}</view>
      </view>
      <view v-for="line in lines" :key="line.id" class="card review-line">
        <view class="task-no">{{ line.product_name }}</view>
        <view>编码：{{ recorded(line.product_code) }} · SKU：{{ recorded(line.product_sku) }}</view>
        <view>规格：{{ recorded(line.product_spec) }} · 基本单位：{{ recorded(line.base_uom_name) }}</view>
        <view>订单第 {{ line.order_line_no }} 行数量：{{ quantity(line.order_qty) }} {{ line.base_uom_name }}（同一订单行仅汇总一次）</view>
        <view>来源库位：{{ recorded(line.from_loc_code) }}</view>
        <view>本库位计划：{{ quantity(line.qty_plan) }} · 实拣：{{ quantity(line.qty_done) }} {{ line.base_uom_name }}</view>
        <view>差异：{{ quantity(line.qty_difference) }} {{ line.base_uom_name }}</view>
        <view v-if="Number(line.qty_done) !== Number(line.qty_plan)" class="warning">数量存在差异，请核对实物后确认</view>
        <button class="toggle-picking" @click="expanded[line.id] = !expanded[line.id]">{{ expanded[line.id] ? '收起' : '查看' }}实际拣货记录（{{ line.picking_details.length }} 组）</button>
        <view v-if="expanded[line.id]" class="picking-records">
          <view v-if="!line.picking_details.length" class="hint">未记录有效的实际拣货记录，请核对后处理</view>
          <view v-for="(detail, index) in line.picking_details" :key="index" class="picking-record">
            <view>库位：{{ recorded(detail.location_code) }} · 数量：{{ quantity(detail.qty) }} {{ line.base_uom_name }}</view>
            <view>批次：{{ recorded(detail.lot_no) }}</view>
            <view>生产日期：{{ recorded(detail.mfg_date) }} · 有效期：{{ recorded(detail.exp_date) }}</view>
            <view>容器：{{ recorded(detail.container_no) }} · 序列号：{{ recorded(detail.serial_no) }}</view>
            <view>操作人：{{ detail.operators?.join('、') || '未记录' }}</view>
            <view>开始：{{ displayTime(detail.first_at) }}</view>
            <view>最近操作：{{ displayTime(detail.last_at) }} · {{ detail.record_count }} 条记录</view>
          </view>
        </view>
      </view>
      <view class="card">
        <template v-if="lines.length && totals.length">
          <view class="task-no">核对汇总 · {{ lines.length }} 条拣货明细</view>
          <view v-for="total in totals" :key="total.base_uom_id" class="quantity-summary">
            {{ total.base_uom_name }}：订单 {{ quantity(total.order_qty) }} · 计划 {{ quantity(total.qty_plan) }} · 实拣 {{ quantity(total.qty_done) }}
          </view>
        </template>
        <view v-else class="warning">无有效明细，不能复核</view>
        <view v-if="!ready && lines.length" class="warning">当前数据尚未核验成功，请刷新成功后再提交</view>
        <button v-if="task.can_print_delivery" @click="openOutboundPrintPage(task.id)">补打送货单</button>
        <button v-else class="primary submit-review" :disabled="!canSubmit" @click="submit()">{{ busy ? '正在处理…' : task.can_retry_post ? '重试过账' : '复核通过' }}</button>
      </view>
    </template>
    <view v-if="loading" class="hint">加载中…</view>
    <view v-if="error" class="warning">{{ error }}</view>
    <button :disabled="loading || busy" @click="load()">刷新任务</button>
  </view>
</template>
<script setup>
import { ref } from 'vue'
import { onLoad, onShow, onUnload, onPullDownRefresh } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { prepareOutboundPrintWindow, closePreparedOutboundPrintWindow, openOutboundPrintPage } from '@/utils/outboundPrint'
import { usePhysicalReview, formatQuantity } from '@/utils/physicalReview'
const id = ref(0)
const expanded = ref({})
const session = usePhysicalReview({
  api, auth: useAuth(), id,
  print: { prepare: prepareOutboundPrintWindow, close: closePreparedOutboundPrintWindow, open: openOutboundPrintPage },
  confirm: (content, reserve) => new Promise(resolve => uni.showModal({ title: '确认复核', content, confirmText: '确定', cancelText: '取消', success: r => { if (r.confirm) reserve(); resolve(Boolean(r.confirm)) }, fail: () => resolve(false) })),
  notify: title => uni.showToast({ title, icon: 'none' }),
})
const { task, order, lines, totals, ready, loading, busy, denied, error, canSubmit, load, submit } = session
const recorded = value => value === null || value === undefined || value === '' ? '未记录' : value
const quantity = value => value === null || value === undefined || value === '' ? '未记录' : formatQuantity(value)
function displayTime(value) {
  if (!value) return '未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未记录'
  return date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}
onLoad(options => { id.value = Number(options.task_id) })
onShow(() => load())
onPullDownRefresh(async () => { await load(); uni.stopPullDownRefresh() })
onUnload(session.dispose)
</script>
<style scoped src="./review.css"></style>

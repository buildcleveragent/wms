<template>
  <view class="history-page">
    <view v-if="loading" class="history-card history-empty">正在读取订单…</view>
    <view v-if="error" class="history-error">{{ error }}<button class="history-button" @click="retry">重试</button></view>
    <template v-if="order">
      <view class="history-card">
        <view class="history-head"><text class="history-title">{{ order.order_no }}</text><text class="history-badge">{{ order.processing_mode_label }}</text></view>
        <text class="history-meta">业务日期 {{ order.biz_date }} · {{ order.outbound_type_label }}</text>
        <text class="history-meta">货主 {{ order.owner_name }} · 仓库 {{ order.warehouse_name }}</text>
        <text class="history-meta">{{ order.supplier_name ? '供应商 ' + order.supplier_name : '客户 ' + (order.customer_name || '-') }}</text>
        <text class="history-meta">{{ order.submit_status_name }} · {{ order.approval_status_name }} · {{ order.is_closed ? '已关闭' : '未关闭' }}</text>
        <text v-if="order.close_reason" class="history-meta">关闭原因：{{ order.close_reason }}</text>
        <text v-if="order.owner_reject_reason" class="history-meta">货主驳回原因：{{ order.owner_reject_reason }}</text>
        <text v-if="order.warehouse_reject_reason" class="history-meta">仓库驳回原因：{{ order.warehouse_reject_reason }}</text>
        <text class="history-meta">收件人 {{ order.contact || '-' }} · {{ order.contact_phone || '-' }}</text>
        <text class="history-meta">收货地址：{{ order.ship_to || '-' }}</text>
        <text v-if="order.src_bill_no" class="history-meta">源单号：{{ order.src_bill_no }}</text>
        <text v-if="order.memo" class="history-meta">备注：{{ order.memo }}</text>
        <text class="history-meta">商品行 {{ order.line_count }} · 订单基本数量 {{ quantity(order.total_base_qty) }}</text>
        <text class="history-meta">{{ order.amount_status === 'UNPRICED' ? '未定价' : order.amount_status === 'PARTIAL' ? '部分定价，已定价小计 ¥ ' + order.priced_amount : '订单金额 ¥ ' + order.total_amount }}</text>
      </view>
      <view class="history-card">
        <text class="history-title">商品明细</text><text class="history-muted">名称、品牌型号、规格优先显示单据快照；旧单无快照时显示可用资料。</text>
        <view v-for="line in order.lines || []" :key="line.id" class="history-row">
          <text class="history-order-no">{{ line.product_name || '-' }}</text>
          <text class="history-meta">品牌型号：{{ line.brand_model || '-' }} · 规格：{{ line.product_spec || '-' }}</text>
          <text class="history-meta">{{ line.product_sku }} · {{ quantity(line.base_qty) }} {{ line.base_uom_name }}</text>
          <text class="history-meta">{{ line.is_priced ? '单价 ¥ ' + line.base_price + ' · 小计 ¥ ' + line.amount : '未定价' }}</text>
          <text v-if="line.lot_no || line.note" class="history-meta">{{ line.lot_no ? '批次 ' + line.lot_no : '' }} {{ line.note }}</text>
        </view>
        <view v-if="!order.lines?.length" class="history-empty">暂无商品明细</view>
      </view>
    </template>
  </view>
</template>
<script setup>
import { ref } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { useOutboundHistory, quantity } from '@/utils/outboundHistory'
const detailId = ref(0)
const session = useOutboundHistory({ api, auth: useAuth(), detailId })
const { order, loading, error, retry } = session
onLoad(query => { detailId.value = Number(query?.id || 0) })
onShow(session.initialize); onHide(session.suspend); onUnload(session.dispose)
</script>
<style src="../../styles/outboundHistory.css"></style>

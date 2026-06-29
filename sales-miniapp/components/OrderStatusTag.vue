<template>
  <text class="status" :class="statusClass">{{ text }}</text>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: {
    type: String,
    default: '',
  },
  text: {
    type: String,
    default: '',
  },
})

const statusClass = computed(() => {
  if (['CANCELLED', 'REJECTED'].includes(props.status)) return 'bad'
  if (['REFUNDING', 'REFUNDED'].includes(props.status)) return 'refund'
  if (props.status === 'WAIT_PAY') return 'pay'
  if (['WAIT_SHIP', 'WAIT_PICK', 'WAIT_WAREHOUSE', 'PENDING_REVIEW'].includes(props.status)) return 'work'
  if (props.status === 'COMPLETED') return 'done'
  return ''
})
</script>

<style scoped>
.status {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 112rpx;
  height: 44rpx;
  padding: 0 14rpx;
  border-radius: 8rpx;
  color: #31515a;
  background: #e9f1f3;
  font-size: 22rpx;
}

.work {
  color: #8a4b0f;
  background: #fff4df;
}

.pay {
  color: #b42318;
  background: #fff0ec;
}

.refund {
  color: #5b21b6;
  background: #f0eafe;
}

.done {
  color: #0f766e;
  background: #e8f7f2;
}

.bad {
  color: #b42318;
  background: #fff0ec;
}
</style>

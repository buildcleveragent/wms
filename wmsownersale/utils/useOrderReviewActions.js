import { computed, ref } from 'vue'
import { api } from '@/utils/request'

export function useOrderReviewActions(options = {}) {
  const submitting = ref(false)

  const getOrderId = options.getOrderId || (() => 0)
  const getOrder = options.getOrder || (() => null)
  const afterApprove = options.afterApprove || (async () => {})
  const afterReject = options.afterReject || (async () => {})
  const afterCancel = options.afterCancel || (async () => {})

  const canReview = computed(() => {
    const order = getOrder()
    if (!order) return false
    return (order.approval_status || '') === 'OWNER_PENDING'
  })

  function confirm(content, title = '确认') {
    return new Promise((resolve) => {
      uni.showModal({
        title,
        content,
        confirmText: '确定',
        cancelText: '取消',
        success: (r) => resolve(!!r?.confirm),
        fail: () => resolve(false),
      })
    })
  }

  async function approveOrder(idArg) {
    const id = Number(idArg || getOrderId())
    if (!id) return false

    const ok = await confirm('确认审核通过并分配库存？')
    if (!ok) return false

    submitting.value = true
    try {
      await api.ownerApprove(id)
      uni.showToast({ title: '审核成功', icon: 'none' })
      await afterApprove(id)
      return true
    } catch (e) {
      const code = e?.statusCode || e?.status
      const msg = e?.data?.detail || (code === 409 ? '库存不足/冲突' : '审核失败')
      uni.showToast({ title: msg, icon: 'none' })
      return false
    } finally {
      submitting.value = false
    }
  }

  async function rejectOrder(idArg) {
    const id = Number(idArg || getOrderId())
    if (!id) return false

    const ok = await confirm('确认退回业务员修改？')
    if (!ok) return false

    submitting.value = true
    try {
      await api.ownerReject(id)
      uni.showToast({ title: '已退回修改', icon: 'none' })
      await afterReject(id)
      return true
    } catch (e) {
      uni.showToast({ title: e?.data?.detail || e?.message || '退回失败', icon: 'none' })
      return false
    } finally {
      submitting.value = false
    }
  }

  async function cancelOrder(idArg) {
    const id = Number(idArg || getOrderId())
    if (!id) return false

    const ok = await confirm('确认取消订单？')
    if (!ok) return false

    submitting.value = true
    try {
      await api.cancelOrder(id)
      uni.showToast({ title: '已取消', icon: 'none' })
      await afterCancel(id)
      return true
    } catch (e) {
      uni.showToast({ title: e?.data?.detail || e?.message || '取消失败', icon: 'none' })
      return false
    } finally {
      submitting.value = false
    }
  }

  return {
    submitting,
    canReview,
    approveOrder,
    rejectOrder,
    cancelOrder,
    confirm,
  }
}

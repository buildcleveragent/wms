const labels = {
  DRAFT: '草稿', SUBMITTED: '已提交', OWNER_PENDING: '待货主审核',
  OWNER_APPROVED: '货主已审核', OWNER_REJECTED: '货主已退回',
  WHS_PENDING: '待仓库确认', WHS_APPROVED: '仓库已确认', WHS_REJECTED: '仓库已退回',
  CANCELLED: '已取消',
}
export function orderStatus(order, field) {
  const label = order?.[field + '_name']
  if (label && /[\u4e00-\u9fff]/.test(label)) return label
  return labels[order?.[field]] || '状态待核对'
}

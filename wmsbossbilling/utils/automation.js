export function automationLabel(state) {
  return {
    disabled: '自动检查已关闭', waiting: '等待首次检查', running: '后台检查正在运行',
    failed: '后台检查失败，结果可能不完整', worker_unavailable: '后台检查服务未连接',
    delayed: '后台检查已延迟，结果可能不完整', healthy: '后台检查正常',
  }[state] || '检查状态未知'
}

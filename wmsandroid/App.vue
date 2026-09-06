<template>
  <view class="app"><slot /></view>
</template>

<script setup>
import { onLaunch } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { installMobileRouteGuards } from '@/utils/mobileAccess'

onLaunch(() => {
  const auth = useAuth()
  installMobileRouteGuards(() => auth)
  if (!auth.access) return
  auth.loadProfile({ force: true }).catch((error) => {
    console.warn('启动时暂时无法加载权限资料', error)
  })
})
</script>

<style>
page {
  --wms-bg: #ffffff;
  --wms-surface: #ffffff;
  --wms-surface-soft: #f8fafc;
  --wms-text: #0f172a;
  --wms-muted: #64748b;
  --wms-border: #e2e8f0;
  --wms-blue: #1677ff;
  --wms-blue-soft: #eff6ff;
  --wms-teal: #0f766e;
  --wms-teal-dark: #0b655f;
  --wms-green: #159357;
  --wms-green-soft: #edf9f3;
  --wms-amber: #e77800;
  --wms-amber-soft: #fff7ed;
  --wms-danger: #e5484d;
  --wms-danger-soft: #fff1f2;
  min-height: 100%;
  background: var(--wms-bg);
  color: var(--wms-text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 28rpx;
  line-height: 1.5;
}

view, text, input, button, picker, textarea { box-sizing: border-box; }
button { margin: 0; font-family: inherit; }
button::after { border: 0; }

.app { min-height: 100%; width: 100%; background: var(--wms-bg); }
.page { min-height: 100vh; width: 100%; padding: 24rpx 28rpx calc(40rpx + env(safe-area-inset-bottom)); }
.title { margin: 0; color: var(--wms-text); font-size: 40rpx; font-weight: 750; line-height: 1.25; }
.section-title { color: var(--wms-text); font-size: 30rpx; font-weight: 700; }
.sub, .text-gray { color: var(--wms-muted); font-size: 24rpx; }
.card, .section { background: var(--wms-surface); border: 1rpx solid var(--wms-border); border-radius: 24rpx; }
.section { margin-top: 24rpx; padding: 24rpx; }
.row { display: flex; align-items: center; justify-content: space-between; gap: 20rpx; }
.bar { display: flex; align-items: center; gap: 16rpx; }
.input {
  width: 100%; min-height: 88rpx; margin: 0; padding: 0 24rpx;
  border: 1rpx solid #cbd5e1; border-radius: 16rpx; background: #fff;
  color: var(--wms-text); font-size: 28rpx;
}
.btn, .btn-primary, .primary-btn {
  min-height: 88rpx; padding: 0 30rpx; border-radius: 18rpx;
  background: var(--wms-teal); color: #fff; font-size: 30rpx; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}
.btn-outline, .ghost-btn {
  min-height: 80rpx; padding: 0 26rpx; border: 1rpx solid #94a3b8; border-radius: 16rpx;
  background: #fff; color: var(--wms-text); font-size: 28rpx;
  display: flex; align-items: center; justify-content: center;
}
.btn-disabled, button[disabled] { opacity: .45; }
.badge {
  display: inline-flex; align-items: center; min-height: 52rpx; padding: 0 18rpx;
  border-radius: 14rpx; background: var(--wms-green-soft); color: var(--wms-green); font-size: 24rpx;
}
.safe-bottom { padding-bottom: calc(24rpx + env(safe-area-inset-bottom)); }

@media (min-width: 768px) {
  .page { max-width: 900rpx; margin: 0 auto; }
}
</style>

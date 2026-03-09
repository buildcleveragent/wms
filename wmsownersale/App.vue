<template>
<view class="app">
<slot />
</view>
</template>
<script setup>
</script>
<style>
.app{min-height:100vh;background:#f6f7fb;color:#111}
.btn{padding:10rpx 20rpx; background:#3b82f6;color:#fff;border-radius:8rpx}
.btn[disabled]{opacity:.5}
.btn-outline{padding:5rpx 20rpx;border:1rpx solid #cbd5e1;border-radius:8rpx;color:#111;background:#fff}
.input{border:1rpx solid #e5e7eb;border-radius:8rpx;padding:16rpx;margin:16rpx 0;background:#fff}
.card{background:#fff;border:1rpx solid #e5e7eb;border-radius:12rpx;padding:20rpx;margin:16rpx 0}
.text-gray{color:#6b7280}
/* .bar{display:flex;gap:16rpx;align-items:center} */
.bar {
  display: flex;  /* 启用 Flexbox 布局 */
  align-items: center;  /* 垂直居中对齐 */
  justify-content: flex-start;  /* 水平排列，从左开始 */
  gap: 10px;  /* 设置按钮和输入框之间的间距 */
}
.row{display:flex;justify-content:space-between;align-items:center}
.badge{background:#eef2ff;color:#3730a3;border-radius:9999rpx;padding:6rpx 14rpx;font-size:24rpx}
.table{width:100%;border-collapse:collapse}
.table th,.table td{border-bottom:1rpx solid #e5e7eb;padding:12rpx}
.title{ font-weight:700; font-size:36rpx; margin:16rpx 0 24rpx }
.seg{ display:flex; background:#f3f4f6; border-radius:12rpx; overflow:hidden }
.seg-item{ flex:1; text-align:center; padding:20rpx 0; color:#6b7280 }
.seg-item.active{ background:#fff; color:#111; font-weight:600; }
/* .grid{ display:grid; grid-template-columns: repeat(4, 1fr); gap:28rpx; margin-top:28rpx } */

.cell{ position:relative; background:#fff; border:1rpx solid #e5e7eb; border-radius:16rpx; padding:24rpx 0; text-align:center }
.icon{ width:72rpx; height:72rpx; margin:auto }
.mask{ position:absolute; inset:0; background:rgba(255,255,255,.7); color:#6b7280; font-size:24rpx; display:flex; align-items:center; justify-content:center; border-radius:16rpx }

page, .page { box-sizing: border-box; width: 100%; padding: 16rpx; }
view { box-sizing: border-box; }

.section { margin-top: 28rpx; background: #fff; border-radius: 16rpx; padding: 18rpx; box-shadow: 0 4rpx 16rpx rgba(0,0,0,.03); }
.section-title { font-weight: 700; font-size: 32rpx; margin-bottom: 12rpx; }

/* Grid without flex gap (better compatibility) */
.grid { display: flex; flex-wrap: wrap; margin-left: -12rpx; margin-right: -12rpx; }
.tile { flex: 0 0 25%; padding-left: 12rpx; padding-right: 12rpx; margin-bottom: 16rpx; }
.tile-inner { background: #fff; border: 1rpx solid #eee; border-radius: 20rpx; padding: 18rpx 0; display: flex; flex-direction: column; align-items: center; }

.icon { width: 92rpx; height: 92rpx; border-radius: 20rpx; display: flex; align-items: center; justify-content: center; font-size: 44rpx; color: #fff; }
.icon.blue { background: #26A4FF; } .icon.orange { background: #FF8A65; } .icon.green { background: #2ECC71; } .icon.gray { background: #BDBDBD; }
.tile-text { margin-top: 12rpx; font-size: 26rpx; color: #333; }

.card { border: 1rpx solid #eee; border-radius: 12rpx; padding: 18rpx; margin: 12rpx 0; }
.sub { color: #666; font-size: 24rpx; }

</style>
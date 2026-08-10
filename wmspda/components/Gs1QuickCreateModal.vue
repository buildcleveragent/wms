<template>
  <view v-if="visible" class="gs1-mask" @click.self="$emit('close')">
    <view class="gs1-sheet" data-testid="gs1-quick-create-modal">
      <view class="gs1-header">
        <view>
          <text class="gs1-eyebrow">GS1 商品资料</text>
          <text class="gs1-title">补齐信息并加入收货单</text>
        </view>
        <button class="gs1-close" @click="$emit('close')">×</button>
      </view>

      <scroll-view scroll-y class="gs1-body">
        <view class="candidate-card">
          <image v-if="candidate?.images?.[0]" :src="candidate.images[0]" mode="aspectFill" class="candidate-image" data-testid="gs1-candidate-image" />
          <view class="candidate-info">
            <text class="candidate-name">{{ candidate?.name || '未命名商品' }}</text>
            <text class="candidate-meta">{{ candidate?.specification || '规格未提供' }}</text>
            <text class="candidate-meta">{{ candidate?.brand || '品牌未提供' }} · {{ candidate?.manufacturer || '厂家未提供' }}</text>
            <text class="candidate-barcode">{{ candidate?.barcode }}</text>
            <text :class="['registration-pill', candidate?.registered ? 'registered' : 'unregistered']">
              {{ candidate?.registered ? '已正式注册' : '查到资料 · 未正式注册' }}
            </text>
          </view>
        </view>

        <view class="form-section">
          <text class="section-title">商品档案</text>
          <text class="field-label">商品分类 *</text>
          <input v-model="categoryKeyword" class="field-input" placeholder="搜索分类名称或编码" />
          <scroll-view scroll-y class="option-list">
            <view
              v-for="item in filteredCategories"
              :key="item.id"
              :class="['option-row', form.category_id === item.id ? 'selected' : '']"
              data-testid="gs1-category-option"
              @click="form.category_id = item.id"
            >
              <text>{{ item.label }}</text><text v-if="form.category_id === item.id">✓</text>
            </view>
            <text v-if="!filteredCategories.length" class="empty-tip">没有匹配的有效分类</text>
          </scroll-view>

          <text class="field-label">基本单位 *</text>
          <input v-model="uomKeyword" class="field-input" placeholder="搜索单位名称或编码" />
          <view class="option-grid">
            <view
              v-for="item in filteredUoms"
              :key="item.id"
              :class="['uom-chip', form.base_uom_id === item.id ? 'selected' : '']"
              data-testid="gs1-uom-option"
              @click="form.base_uom_id = item.id"
            >{{ item.label }}</view>
          </view>
        </view>

        <view class="form-section">
          <text class="section-title">本次收货</text>
          <text class="field-label">收货数量（基本单位）*</text>
          <input v-model="form.quantity" class="field-input" type="digit" placeholder="请输入大于 0 的数量" data-testid="gs1-quantity" @input="form.quantity = $event.detail.value" />

          <view class="switch-row" data-testid="gs1-batch-switch" @click="form.batch_control = !form.batch_control">
            <view><text class="switch-title">批次管理</text><text class="switch-help">按批号追踪库存</text></view>
            <switch :checked="form.batch_control" color="#2563eb" @click.stop @change="form.batch_control = $event.detail.value" />
          </view>
          <view v-if="form.batch_control">
            <text class="field-label">本次批号 *</text>
            <input v-model="form.lot_no" class="field-input" placeholder="扫描或输入包装批号" />
          </view>

          <view class="switch-row" data-testid="gs1-expiry-switch" @click="form.expiry_control = !form.expiry_control">
            <view><text class="switch-title">效期管理</text><text class="switch-help">记录生产与到期日期</text></view>
            <switch :checked="form.expiry_control" color="#2563eb" @click.stop @change="form.expiry_control = $event.detail.value" />
          </view>
          <template v-if="form.expiry_control">
            <text class="field-label">效期基准 *</text>
            <view class="segmented">
              <view :class="['segment', form.expiry_basis === 'MFG' ? 'active' : '']" @click="form.expiry_basis = 'MFG'">生产日期</view>
              <view :class="['segment', form.expiry_basis === 'INBOUND' ? 'active' : '']" @click="form.expiry_basis = 'INBOUND'">入库日期</view>
            </view>
            <template v-if="form.expiry_basis === 'MFG'">
              <text class="field-label">保质期天数 *</text>
              <input v-model="form.shelf_life_days" class="field-input" type="number" placeholder="例如 365" />
            </template>
            <template v-else>
              <text class="field-label">入库有效天数 *</text>
              <input v-model="form.inbound_valid_days" class="field-input" type="number" placeholder="例如 30" />
            </template>
            <text class="field-label">效期预警天数</text>
            <input v-model="form.expiry_warning_days" class="field-input" type="number" placeholder="选填，必须小于有效天数" />

            <view class="date-row">
              <view class="date-col">
                <text class="field-label">生产日期{{ form.expiry_basis === 'MFG' ? ' *' : '' }}</text>
                <picker mode="date" :value="form.mfg_date" @change="form.mfg_date = $event.detail.value">
                  <view class="date-picker">{{ form.mfg_date || '选择日期' }}</view>
                </picker>
              </view>
              <view class="date-col">
                <text class="field-label">到期日期{{ form.expiry_basis === 'MFG' ? ' *' : '' }}</text>
                <picker mode="date" :value="form.exp_date" @change="form.exp_date = $event.detail.value">
                  <view class="date-picker">{{ form.exp_date || '选择日期' }}</view>
                </picker>
              </view>
            </view>
          </template>
        </view>
      </scroll-view>

      <view class="gs1-footer">
        <button class="secondary-btn" :disabled="submitting" @click="$emit('close')">取消</button>
        <button class="primary-btn" :loading="submitting" :disabled="submitting" data-testid="gs1-submit" @click="submit">建档并加入购物车</button>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

const props = defineProps({
  visible: Boolean,
  candidate: { type: Object, default: () => ({}) },
  options: { type: Object, default: () => ({ categories: [], uoms: [] }) },
  submitting: Boolean,
})
const emit = defineEmits(['close', 'submit'])
const categoryKeyword = ref('')
const uomKeyword = ref('')
const form = reactive<any>({})

function resetForm() {
  Object.assign(form, {
    category_id: null,
    base_uom_id: null,
    quantity: '1',
    batch_control: true,
    lot_no: '',
    expiry_control: true,
    expiry_basis: 'MFG',
    shelf_life_days: '',
    inbound_valid_days: '',
    expiry_warning_days: '',
    mfg_date: '',
    exp_date: '',
  })
  categoryKeyword.value = ''
  uomKeyword.value = ''
}

watch(() => [props.visible, props.candidate?.lookup_id], ([visible]) => {
  if (visible) resetForm()
})

const filteredCategories = computed(() => {
  const q = categoryKeyword.value.trim().toLowerCase()
  const rows = props.options?.categories || []
  return q ? rows.filter((row:any) => `${row.code} ${row.label}`.toLowerCase().includes(q)) : rows
})
const filteredUoms = computed(() => {
  const q = uomKeyword.value.trim().toLowerCase()
  const rows = props.options?.uoms || []
  return q ? rows.filter((row:any) => `${row.code} ${row.label}`.toLowerCase().includes(q)) : rows
})

function submit() {
  if (!form.category_id) return uni.showToast({ title: '请选择商品分类', icon: 'none' })
  if (!form.base_uom_id) return uni.showToast({ title: '请选择基本单位', icon: 'none' })
  if (!(Number(form.quantity) > 0)) return uni.showToast({ title: '请输入正确的收货数量', icon: 'none' })
  if (form.batch_control && !String(form.lot_no || '').trim()) return uni.showToast({ title: '请输入本次批号', icon: 'none' })
  if (form.expiry_control && form.expiry_basis === 'MFG' && (!form.shelf_life_days || !form.mfg_date || !form.exp_date)) {
    return uni.showToast({ title: '请补齐保质期和本次日期', icon: 'none' })
  }
  if (form.expiry_control && form.expiry_basis === 'INBOUND' && !form.inbound_valid_days) {
    return uni.showToast({ title: '请输入入库有效天数', icon: 'none' })
  }
  emit('submit', { ...form })
}
</script>

<style scoped>
.gs1-mask { position: fixed; inset: 0; z-index: 999; display: flex; align-items: flex-end; height: 100vh; overflow: hidden; background: rgba(15, 23, 42, .58); }
.gs1-sheet { width: 100%; height: 92vh; display: flex; flex-direction: column; overflow: hidden; border-radius: 28rpx 28rpx 0 0; background: #f8fafc; }
.gs1-header { display: flex; flex: 0 0 auto; justify-content: space-between; align-items: center; padding: 28rpx 30rpx 20rpx; background: #fff; border-bottom: 1px solid #e2e8f0; }
.gs1-eyebrow, .gs1-title { display: block; }.gs1-eyebrow { color: #2563eb; font-size: 22rpx; font-weight: 700; }.gs1-title { margin-top: 4rpx; color: #0f172a; font-size: 34rpx; font-weight: 750; }
.gs1-close { width: 64rpx; height: 64rpx; margin: 0; padding: 0; border-radius: 50%; color: #64748b; background: #f1f5f9; font-size: 42rpx; line-height: 60rpx; }
.gs1-body { flex: 1 1 auto; width: 100%; height: 0; min-height: 0; padding: 22rpx 24rpx 36rpx; box-sizing: border-box; }
.candidate-card { display: flex; gap: 20rpx; padding: 22rpx; border: 1px solid #dbeafe; border-radius: 20rpx; background: linear-gradient(135deg, #eff6ff, #fff); }
.candidate-image { width: 150rpx; height: 150rpx; flex: 0 0 auto; border-radius: 16rpx; background: #e2e8f0; }.candidate-info { min-width: 0; flex: 1; }.candidate-name, .candidate-meta, .candidate-barcode { display: block; }.candidate-name { color: #0f172a; font-size: 30rpx; font-weight: 750; }.candidate-meta { margin-top: 7rpx; color: #475569; font-size: 23rpx; }.candidate-barcode { margin-top: 8rpx; color: #1d4ed8; font-family: monospace; font-size: 24rpx; }
.registration-pill { display: inline-block; margin-top: 10rpx; padding: 5rpx 12rpx; border-radius: 999rpx; font-size: 21rpx; }.registered { color: #166534; background: #dcfce7; }.unregistered { color: #9a3412; background: #ffedd5; }
.form-section { margin-top: 20rpx; padding: 24rpx; border-radius: 20rpx; background: #fff; box-shadow: 0 4rpx 18rpx rgba(15, 23, 42, .05); }.section-title { display: block; margin-bottom: 18rpx; color: #0f172a; font-size: 28rpx; font-weight: 750; }.field-label { display: block; margin: 17rpx 0 8rpx; color: #334155; font-size: 23rpx; font-weight: 650; }.field-input, .date-picker { height: 76rpx; padding: 0 20rpx; border: 1px solid #cbd5e1; border-radius: 14rpx; background: #fff; box-sizing: border-box; font-size: 26rpx; line-height: 76rpx; }
.option-list { max-height: 240rpx; margin-top: 8rpx; border: 1px solid #e2e8f0; border-radius: 14rpx; }.option-row { display: flex; justify-content: space-between; padding: 18rpx 20rpx; color: #334155; font-size: 24rpx; border-bottom: 1px solid #f1f5f9; }.option-row.selected { color: #1d4ed8; background: #eff6ff; font-weight: 700; }.empty-tip { display: block; padding: 24rpx; color: #94a3b8; text-align: center; }
.option-grid { display: flex; flex-wrap: wrap; gap: 12rpx; margin-top: 10rpx; }.uom-chip { padding: 13rpx 18rpx; border: 1px solid #cbd5e1; border-radius: 999rpx; color: #475569; font-size: 23rpx; }.uom-chip.selected { border-color: #2563eb; color: #1d4ed8; background: #eff6ff; font-weight: 700; }
.switch-row { display: flex; justify-content: space-between; align-items: center; margin-top: 24rpx; padding: 18rpx 0; border-top: 1px solid #f1f5f9; }.switch-title, .switch-help { display: block; }.switch-title { color: #1e293b; font-size: 26rpx; font-weight: 700; }.switch-help { margin-top: 4rpx; color: #94a3b8; font-size: 21rpx; }
.segmented { display: flex; padding: 6rpx; border-radius: 14rpx; background: #f1f5f9; }.segment { flex: 1; padding: 15rpx; border-radius: 10rpx; color: #64748b; text-align: center; font-size: 24rpx; }.segment.active { color: #1d4ed8; background: #fff; font-weight: 700; box-shadow: 0 2rpx 8rpx rgba(15, 23, 42, .08); }.date-row { display: flex; gap: 14rpx; }.date-col { flex: 1; min-width: 0; }
.gs1-footer { display: flex; flex: 0 0 auto; gap: 18rpx; padding: 20rpx 24rpx calc(20rpx + env(safe-area-inset-bottom)); background: #fff; border-top: 1px solid #e2e8f0; }.secondary-btn, .primary-btn { height: 82rpx; margin: 0; border-radius: 14rpx; font-size: 27rpx; line-height: 82rpx; }.secondary-btn { flex: 0 0 180rpx; color: #475569; background: #f1f5f9; }.primary-btn { flex: 1; color: #fff; background: #2563eb; font-weight: 700; }
</style>

<template>
  <view class="page edit-page">
    <view class="form">
      <input class="input" v-model="form.contact" placeholder="联系人" />
      <input class="input" v-model="form.phone" placeholder="联系电话" />
      <view class="grid">
        <input class="input" v-model="form.province" placeholder="省" />
        <input class="input" v-model="form.city" placeholder="市" />
        <input class="input" v-model="form.district" placeholder="区" />
      </view>
      <textarea class="textarea" v-model="form.detail" placeholder="详细地址" />
      <label class="default-row">
        <checkbox :checked="form.is_default" @change="form.is_default = $event.detail.value.length > 0" />
        <text>设为默认地址</text>
      </label>
    </view>
    <button class="save" :loading="loading" @click="save">保存</button>
  </view>
</template>

<script setup>
import { onLoad } from '@dcloudio/uni-app'
import { reactive, ref } from 'vue'
import { addressService } from '../../services/address'

const id = ref('')
const ownerId = ref('')
const loading = ref(false)
const form = reactive({
  contact: '',
  phone: '',
  province: '',
  city: '',
  district: '',
  detail: '',
  is_default: false,
})

async function loadAddress(addressId) {
  const rows = await addressService.list({ owner_id: ownerId.value })
  const row = rows.find((item) => String(item.id) === String(addressId))
  if (row) {
    Object.assign(form, {
      contact: row.contact,
      phone: row.phone,
      province: row.province,
      city: row.city,
      district: row.district,
      detail: row.detail,
      is_default: row.is_default,
    })
  }
}

async function save() {
  if (!form.contact || !form.phone || !form.detail) {
    uni.showToast({ title: '请填写联系人、电话和地址', icon: 'none' })
    return
  }
  loading.value = true
  try {
    if (id.value) {
      await addressService.update(id.value, { ...form, owner_id: ownerId.value })
    } else {
      await addressService.create({ ...form, owner_id: ownerId.value })
    }
    uni.navigateBack()
  } catch (err) {
    uni.showToast({ title: err.message || '保存失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

onLoad((query = {}) => {
  id.value = query.id || ''
  ownerId.value = query.owner_id || ''
  if (id.value) loadAddress(id.value)
})
</script>

<style scoped>
.edit-page {
  padding-bottom: 130rpx;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12rpx;
}

.textarea {
  width: 100%;
  min-height: 156rpx;
  padding: 18rpx;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  box-sizing: border-box;
  font-size: 26rpx;
}

.default-row {
  height: 72rpx;
  display: flex;
  align-items: center;
  gap: 12rpx;
  color: #334155;
  font-size: 26rpx;
}

.save {
  position: fixed;
  left: 24rpx;
  right: 24rpx;
  bottom: 24rpx;
  height: 84rpx;
  line-height: 84rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 28rpx;
  font-weight: 800;
}

.save::after {
  border: 0;
}
</style>

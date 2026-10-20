<template><TaskList :transport="transport" :identity="identity" :visible="visible" kind="RECEIVE"/></template>
<script setup>
import {computed,ref} from 'vue'
import {onShow,onHide,onUnload} from '@dcloudio/uni-app'
import TaskList from '../../../frontend/shared/arrivals/TaskList.vue'
import {request} from '@/utils/request'
import {useAuth} from '@/store/auth'
const auth=useAuth(),visible=ref(false),transport={request}
const identity=computed(()=>JSON.stringify([auth.user?.id,auth.user?.username,Boolean(auth.access),auth.perms]))
onShow(()=>{visible.value=true});onHide(()=>{visible.value=false});onUnload(()=>{visible.value=false})
</script>

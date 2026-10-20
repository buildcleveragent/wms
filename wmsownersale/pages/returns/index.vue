<template><Workspace :transport="transport" :identity="identity" :visible="visible" :record-id="recordId" :original-id="originalId" :filter-order="filterOrder" :warehouse="false" @open="open" /></template>
<script setup>
import {computed,ref} from 'vue'
import {onLoad,onShow,onHide,onUnload} from '@dcloudio/uni-app'
import Workspace from '../../../frontend/shared/customer-returns/Workspace.vue'
import {request,uploadAuthenticatedFile,returnPhotoURL} from '@/utils/request'
import {useAuth} from '@/store/auth'
const auth=useAuth(), visible=ref(false),recordId=ref(0),originalId=ref(0),filterOrder=ref(0)
const identity=computed(()=>JSON.stringify([auth.user?.id,auth.user?.username,auth.user?.owner_id,auth.roles || auth.profile?.user?.roles,auth.access]))
const transport={request,upload:uploadAuthenticatedFile,image:returnPhotoURL}
onLoad(query=>{recordId.value=Number(query.id)||0;originalId.value=Number(query.order_id)||0;filterOrder.value=Number(query.filter_order)||0})
let displaySequence=0
onShow(async()=>{const seq=++displaySequence;visible.value=false;try{const session=await auth.bootstrap(); if(session.status==='anonymous'){uni.reLaunch({url:'/pages/login'});return}}catch{if(seq===displaySequence)visible.value=true;return}if(seq===displaySequence)visible.value=true});onHide(()=>{displaySequence++;visible.value=false});onUnload(()=>{displaySequence++;visible.value=false})
function open(id){uni.navigateTo({url:'/pages/returns/index?id='+id})}
</script>

<template><Workspace :transport="transport" :identity="identity" :visible="visible" :record-id="recordId" :creating="creating" :can-create="auth.isOwnerSalesperson" :manager="auth.isOwnerManager" @open="open" @create="create"/></template>
<script setup>
import {computed,ref} from 'vue'
import {onLoad,onShow,onHide,onUnload} from '@dcloudio/uni-app'
import Workspace from '../../../frontend/shared/arrivals/Workspace.vue'
import {request,uploadAuthenticatedFile,returnPhotoURL} from '@/utils/request'
import {useAuth} from '@/store/auth'
const auth=useAuth(),visible=ref(false),recordId=ref(0),creating=ref(false)
const identity=computed(()=>JSON.stringify([auth.user?.id,auth.user?.username,auth.user?.owner_id,auth.roles,Boolean(auth.access)]))
const transport={request,upload:uploadAuthenticatedFile,image:returnPhotoURL}
onLoad(q=>{recordId.value=Number(q.id)||0;creating.value=q.create==='1'})
let sequence=0
onShow(async()=>{const seq=++sequence;visible.value=false;try{const session=await auth.bootstrap();if(session.status==='anonymous'){uni.reLaunch({url:'/pages/login'});return}}catch{if(seq===sequence)visible.value=true;return}if(seq===sequence)visible.value=true})
onHide(()=>{sequence++;visible.value=false});onUnload(()=>{sequence++;visible.value=false})
function open(id){const opts={url:'/pages/inbound/index?id='+id};creating.value?uni.redirectTo(opts):uni.navigateTo(opts)}
function create(){uni.navigateTo({url:'/pages/inbound/index?create=1'})}
</script>

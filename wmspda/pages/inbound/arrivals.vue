<template><Workspace :transport="transport" :identity="identity" :visible="visible" :record-id="recordId" :warehouse="true" :manager="auth.perms.includes('inbound.manage_arrival')" @open="open"/></template>
<script setup>
import {computed,ref} from 'vue'
import {onLoad,onShow,onHide,onUnload} from '@dcloudio/uni-app'
import Workspace from '../../../frontend/shared/arrivals/Workspace.vue'
import {request,uploadAuthenticatedFile,returnPhotoURL} from '@/utils/request'
import {useAuth} from '@/store/auth'
const auth=useAuth(),visible=ref(false),recordId=ref(0)
const identity=computed(()=>JSON.stringify([auth.user?.id,auth.user?.username,auth.user?.owner_id,auth.roles || auth.profile?.user?.roles,Boolean(auth.access)]))
const transport={request,upload:uploadAuthenticatedFile,image:returnPhotoURL}
onLoad(q=>{recordId.value=Number(q.id)||0})
let sequence=0
onShow(async()=>{const seq=++sequence;visible.value=false;try{await auth.ensureProfile();if(!auth.access){uni.reLaunch({url:'/pages/login'});return}}catch{if(seq===sequence)visible.value=true;return}if(seq===sequence)visible.value=true})
onHide(()=>{sequence++;visible.value=false});onUnload(()=>{sequence++;visible.value=false})
function open(id){uni.navigateTo({url:'/pages/inbound/arrivals?id='+id})}
</script>

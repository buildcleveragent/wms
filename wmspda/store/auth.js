import { defineStore } from 'pinia'
import { api, setToken } from '@/utils/request'

export const useAuth = defineStore('auth', {
state: ()=>({ user:null, access:'' }),
actions: {
async login(username, password){
const res = await api.login(username, password)
this.access = res?.access||''
setToken(this.access)
this.user = { username }
},
logout(){ this.user=null; this.access=''; setToken('') }
}
})
export async function scanOne(){
try{
const res = await new Promise((resolve,reject)=>{
uni.scanCode({ onlyFromCamera:true, success:resolve, fail:reject })
})
return (res.result||'').trim()
}catch(e){}
return ''
}
"use weex:vue";

if (typeof Promise !== 'undefined' && !Promise.prototype.finally) {
  Promise.prototype.finally = function(callback) {
    const promise = this.constructor
    return this.then(
      value => promise.resolve(callback()).then(() => value),
      reason => promise.resolve(callback()).then(() => {
        throw reason
      })
    )
  }
};

if (typeof uni !== 'undefined' && uni && uni.requireGlobal) {
  const global = uni.requireGlobal()
  ArrayBuffer = global.ArrayBuffer
  Int8Array = global.Int8Array
  Uint8Array = global.Uint8Array
  Uint8ClampedArray = global.Uint8ClampedArray
  Int16Array = global.Int16Array
  Uint16Array = global.Uint16Array
  Int32Array = global.Int32Array
  Uint32Array = global.Uint32Array
  Float32Array = global.Float32Array
  Float64Array = global.Float64Array
  BigInt64Array = global.BigInt64Array
  BigUint64Array = global.BigUint64Array
};


(()=>{var x=Object.create;var v=Object.defineProperty;var f=Object.getOwnPropertyDescriptor;var y=Object.getOwnPropertyNames;var C=Object.getPrototypeOf,b=Object.prototype.hasOwnProperty;var h=(e,a)=>()=>(a||e((a={exports:{}}).exports,a),a.exports);var k=(e,a,o,n)=>{if(a&&typeof a=="object"||typeof a=="function")for(let r of y(a))!b.call(e,r)&&r!==o&&v(e,r,{get:()=>a[r],enumerable:!(n=f(a,r))||n.enumerable});return e};var D=(e,a,o)=>(o=e!=null?x(C(e)):{},k(a||!e||!e.__esModule?v(o,"default",{value:e,enumerable:!0}):o,e));var _=h((V,g)=>{g.exports=Vue});var t=D(_());function p(e){return weex.requireModule(e)}function c(e,a,...o){uni.__log__?uni.__log__(e,a,...o):console[e].apply(console,[...o,a])}var P=(e,a)=>{let o=e.__vccOpts||e;for(let[n,r]of a)o[n]=r;return o},S=plus.android.runtimeMainActivity(),d,i=p("TH-PlatformSDK"),A=p("modal");p("TH-PlatformSDK-PrinterManager");var w={data(){return{textStatus:"",textValue:""}},onLoad(){this.registerBroadcast(),i.addStatusActionListener(e=>{c("log","at pages/sample/ext-module-device-scan.nvue:35","addStatusActionListener()  res == "+e),e==4?this.textStatus="[\u5F00\u59CB\u626B\u63CF]":e==3?this.textStatus="[\u677E\u5F00\u6309\u952E\uFF08\u505C\u6B62\u626B\u63CF\uFF09]":e==2&&(this.textStatus="[\u626B\u63CF\u8D85\u65F6]")})},onUnload(){this.unRegisterBroadcast(),i.removeStatusActionListener()},methods:{getDeviceId(){c("log","at pages/sample/ext-module-device-scan.nvue:54","getDeviceId()");var e=i.getDeviceId();this.textValue="SN\uFF1A"+e},startScan(){i.startScan()},stopScan(){i.stopScan()},registerBroadcast(){c("log","at pages/sample/ext-module-device-scan.nvue:65","\u6CE8\u518C\u626B\u63CF\u5E7F\u64AD"),i.setScanMode(0,0),d=plus.android.implements("io.dcloud.feature.internal.reflect.BroadcastReceiver",{onReceive:n});var e=this,a=plus.android.importClass("android.content.IntentFilter"),o=new a;o.addAction("android.intent.ACTION_DECODE_DATA"),S.registerReceiver(d,o);function n(r,s){plus.android.importClass(s);var l=s.getStringExtra("barcode_string");c("log","at pages/sample/ext-module-device-scan.nvue:87","scan-barcode:"+l),e.textValue=l,e.textStatus="[\u626B\u63CF\u6210\u529F]"}},unRegisterBroadcast(){c("log","at pages/sample/ext-module-device-scan.nvue:93","\u6CE8\u9500\u626B\u63CF\u5E7F\u64AD"),S.unregisterReceiver(d)},setScanParameter(){c("log","at pages/sample/ext-module-device-scan.nvue:97","\u8BBE\u7F6E\u626B\u63CF\u6A21\u5F0F\u53C2\u6570");let e=[6,7],a=[2,1];i.setScanParameterInts(e,a,o=>{c("log","at pages/sample/ext-module-device-scan.nvue:180","setScanParameterInts() ret == "+o),A.toast({message:o,duration:1.5})})},gotoNativePage(){testModule.gotoNativePage()}}};function I(e,a,o,n,r,s){let l=(0,t.resolveComponent)("button");return(0,t.openBlock)(),(0,t.createElementBlock)("scroll-view",{scrollY:!0,showScrollbar:!0,enableBackToTop:!0,bubble:"true",style:{flexDirection:"column"}},[(0,t.createElementVNode)("div",null,[(0,t.createVNode)(l,{type:"primary",onClick:s.getDeviceId},{default:(0,t.withCtx)(()=>[(0,t.createTextVNode)("\u83B7\u53D6\u8BBE\u5907SN\u53F7")]),_:1},8,["onClick"]),(0,t.createVNode)(l,{type:"primary",onClick:s.startScan},{default:(0,t.withCtx)(()=>[(0,t.createTextVNode)("\u6309\u94AE\u89E6\u53D1\u626B\u63CF")]),_:1},8,["onClick"]),(0,t.createVNode)(l,{type:"primary",onClick:s.stopScan},{default:(0,t.withCtx)(()=>[(0,t.createTextVNode)("\u6309\u94AE\u505C\u6B62\u626B\u63CF")]),_:1},8,["onClick"]),(0,t.createVNode)(l,{type:"primary",onClick:s.setScanParameter},{default:(0,t.withCtx)(()=>[(0,t.createTextVNode)("\u8BBE\u7F6E\u626B\u63CF\u6A21\u5F0F\u53C2\u6570")]),_:1},8,["onClick"]),(0,t.createElementVNode)("u-text",null," \u626B\u7801\u72B6\u6001\uFF1A"+(0,t.toDisplayString)(r.textStatus),1),(0,t.createElementVNode)("u-text",null," \u7ED3\u679C\uFF1A"),(0,t.createElementVNode)("u-text",null,(0,t.toDisplayString)(r.textValue),1)])])}var u=P(w,[["render",I]]);var m=plus.webview.currentWebview();if(m){let e=parseInt(m.id),a="pages/sample/ext-module-device-scan",o={};try{o=JSON.parse(m.__query__)}catch(r){}u.mpType="page";let n=Vue.createPageApp(u,{$store:getApp({allowDefault:!0}).$store,__pageId:e,__pagePath:a,__pageQuery:o});n.provide("__globalStyles",Vue.useCssStyles([...__uniConfig.styles,...u.styles||[]])),n.mount("#root")}})();

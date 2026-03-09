const ENV = (uni.getAccountInfoSync && uni.getAccountInfoSync().miniProgram?.envVersion) || 'develop'
const BASE_MAP = {
	// develop: 'http://192.168.1.6:8001',
	develop: 'http://192.168.1.6:8001',
	trial: 'https://trial.example.com',
	mobilephone: 'http://8.148.198.200:8080',
	onsite: 'http://192.168.2.6:8001',
}
// export const BASE_URL = BASE_MAP[ENV] || BASE_MAP.develop
export const BASE_URL = BASE_MAP.develop


function getToken(){ try{ return uni.getStorageSync('access') || '' }catch(e){ return '' } }
export function setToken(t){ uni.setStorageSync('access', t||'') }


export function request(opts){
const token = getToken()
	return new Promise((resolve,reject)=>{
		uni.request({
		url: BASE_URL + opts.url,
		method: opts.method || 'GET',
		data: opts.data || {},
		header: {
		'Content-Type':'application/json',
		...(token ? { Authorization: `Bearer ${token}` } : {})
	},
	success: (res)=>{
		if(res.statusCode>=200 && res.statusCode<300){ resolve(res.data); return }
		uni.showToast({ title: (res.data?.detail||res.data?.message||'请求失败'), icon: 'none' })
		reject(res)
	},
	fail: reject
	})
	})
	}

export const api = {
	// Auth
	login: (username, password)=> request({ url: '/api/token/', method:'POST', data:{ username, password } }),

	// Catalog
	customers: (q='', page=1)=> request({ url: `/api/catalog/customers?search=${encodeURIComponent(q)}&page=${page}` }),
	// products: (q='', page=1, warehouse_id)=> request({ url: `/api/catalog/products?search=${encodeURIComponent(q)}&page=${page}${warehouse_id?`&warehouse_id=${warehouse_id}`:''}` }),
	products: (q = '', page = 1) =>request({ url: `/api/catalog/products?search=${encodeURIComponent(q)}&page=${page}` }),

	// Orders
	createOutboundOrder: (payload)=> request({ url: '/api/outbound/orders/', method:'POST', data: payload }),
	orders: (q='', page=1)=> request({ url:`/api/outbound/orders?search=${encodeURIComponent(q)}&page=${page}` }),
	orderDetail: (id)=> request({ url:`/api/outbound/orders/${id}/` }),
	pendingOrders: (page=1, search='')=> request({ url:`/api/outbound/orders?approval_status=OWNER_PENDING&page=${page}${search?`&search=${encodeURIComponent(search)}`:''}` }),
	
	
	ordersByApprovalStatus: (approvalStatus, page=1, search='') =>
	  request({ url: `/api/outbound/orders?approval_status=${encodeURIComponent(approvalStatus)}&page=${page}${search?`&search=${encodeURIComponent(search)}`:''}` }),
    ownerApprove: (id)=> request({ url:`/api/outbound/orders/${id}/owner-approve/`, method:'POST' }),
	// utils/request.js
	// 略去你现有的 request 封装...
  // 审核列表（支持 search / approval_status / submit_status / page）
    orders(params = {}) {
	    const qs = Object.entries(params)
	      .filter(([, v]) => v !== undefined && v !== null && v !== '')
	      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
	      .join('&')
	    const url = qs ? `/api/outbound/orders/?${qs}` : `/api/outbound/orders/`
	    return request({ url })
	  },
	
	  // 审核通过
	ownerApprove: (id) =>
	    request({ url: `/api/outbound/orders/${id}/owner-approve/`, method: 'POST' }),
	
	  // 撤审（释放占用）
	ownerUnapprove: (id) =>
	    request({ url: `/api/outbound/orders/${id}/owner-unapprove/`, method: 'POST' }),
	
	  // 取消订单（释放占用并置取消态）
	cancelOrder: (id) =>
	    request({ url: `/api/outbound/orders/${id}/cancel/`, method: 'POST' }),
		
   
		
		
	
		
}
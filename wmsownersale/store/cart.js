import { defineStore } from 'pinia'
import { createIdempotencyKey } from '@/utils/idempotency'
import { normalizeSelectedCustomer } from '@/utils/customer'

export const useCart = defineStore('cart', {
state: ()=>({
	user_id: null,
	owner_id: null,
	warehouse_id: null,
	warehouse_name: '',
	idempotency_key: null,
	editing_order_id: null,
	editing_updated_at: null,
	owner_reject_reason: '',
	order_header: {
		src_bill_no: '',
		contact: '',
		contact_phone: '',
		ship_to: '',
		delivery_method: null,
		etd: null,
		remark: '业务员下单',
	},
	customer: null, // {id, code, name}
	items: [] // [{product_id, sku, name, price, qty}]
}),
getters:{
	totalQty:(s)=> s.items.reduce((a,b)=> a + (b.qty||0), 0),
	totalAmount:(s)=> s.items.reduce((a,b)=> a + (b.qty||0)*(b.price||0), 0),
	hasContextForUser:(s)=>(userId, ownerId)=> Boolean(
		s.warehouse_id &&
		s.user_id &&
		s.owner_id &&
		String(s.user_id) === String(userId || '') &&
		String(s.owner_id) === String(ownerId || '')
	)
},
actions:{
	beginOrder({ user_id, owner_id, warehouse }){
		this.user_id = user_id || null
		this.owner_id = owner_id || null
		this.warehouse_id = warehouse?.id || null
		this.warehouse_name = warehouse?.name || ''
		this.idempotency_key = createIdempotencyKey()
		this.editing_order_id = null
		this.editing_updated_at = null
		this.owner_reject_reason = ''
		Object.assign(this.order_header, {
			src_bill_no: '', contact: '', contact_phone: '', ship_to: '',
			delivery_method: null, etd: null, remark: '业务员下单',
		})
		this.customer = null
		this.items = []
	},
	beginEdit({ user_id, owner_id, context }){
		if (!context?.id || !context?.warehouse?.id || !context?.customer?.id) return false
		this.user_id = user_id || null
		this.owner_id = owner_id || null
		this.warehouse_id = context.warehouse.id
		this.warehouse_name = context.warehouse.name || ''
		this.idempotency_key = null
		this.editing_order_id = context.id
		this.editing_updated_at = context.updated_at || null
		this.owner_reject_reason = context.owner_reject_reason || ''
		this.customer = normalizeSelectedCustomer(context.customer)
		if (!this.customer) return false
		Object.assign(this.order_header, {
			src_bill_no: context.header?.src_bill_no || '',
			contact: context.header?.contact || '',
			contact_phone: context.header?.contact_phone || '',
			ship_to: context.header?.ship_to || '',
			delivery_method: context.header?.delivery_method || null,
			etd: context.header?.etd || null,
			remark: context.header?.remark || '',
		})
		this.items = (context.items || []).map(item => ({
			...item,
			product_id: item.product_id || item.id,
			qty: Number(item.qty || 0),
			price: Number(item.price || 0),
			available: Number(item.available || 0),
		}))
		return true
	},
	changeWarehouseForEdit(warehouse){
		if (!this.editing_order_id || !warehouse?.id) return false
		if (String(this.warehouse_id) !== String(warehouse.id)) {
			this.warehouse_id = warehouse.id
			this.warehouse_name = warehouse.name || ''
			this.customer = null
			this.items = []
		}
		return true
	},
	resetOrder(){
		this.user_id = null
		this.owner_id = null
		this.warehouse_id = null
		this.warehouse_name = ''
		this.idempotency_key = null
		this.editing_order_id = null
		this.editing_updated_at = null
		this.owner_reject_reason = ''
		Object.assign(this.order_header, {
			src_bill_no: '', contact: '', contact_phone: '', ship_to: '',
			delivery_method: null, etd: null, remark: '业务员下单',
		})
		this.customer = null
		this.items = []
	},
	ensureIdempotencyKey(){
		if (!this.idempotency_key) this.idempotency_key = createIdempotencyKey()
		return this.idempotency_key
	},
	setCustomer(c){
		const customer = normalizeSelectedCustomer(c)
		if (!customer) return false
		const previousCustomerId = this.customer?.id
		if (
			previousCustomerId &&
			String(previousCustomerId) !== String(customer.id)
		) {
			this.items = []
			this.idempotency_key = createIdempotencyKey()
		}
		this.customer = customer
		return true
	},
	// addItem(p){
	// 	const exist = this.items.find(x=> x.product_id===p.id)
	// 	if(exist){ exist.qty += 1; return }

	// 	this.items.push({ 
	// 		       product_id: p.id, sku:p.sku, 
	// 			         name:p.name, 
	// 		            price:Number(p.price||0), 
	// 					  qty:1,
	// 		product_image_url:p.product_image_url,
	// 		             gtin:p.gtin,
	// 			 aux_uom_name:p.aux_uom_name,
	// 		   base_unit_name:p.base_unit_name,
	// 		  aux_qty_in_base:p.aux_qty_in_base,		
	//         product_min_price:Number(p.product_min_price||0),
	// 		     max_discount:Number(p.max_discount||0),	
	// 			    available:p.available,
	// 			  unitOptions:p.unitOptions,				  
	// 			  selectedUnitIndex: p.selectedUnitIndex,
	// 		  })
	// 	},
    addItem(p){
	       const initQty = Number(p?.qty)
	       if (!p?.id || !Number.isFinite(initQty) || initQty <= 0) return false

	       const exist = this.items.find(x => x.product_id === p.id)
	       if (exist) {
	         exist.qty += initQty
	         return true
	       }
	     
	       this.items.push({
	         product_id: p.id,
	         sku: p.sku,
	         name: p.name,
			 spec: p.spec,
	         price: Number(p.price || 0),
	         orig_price: Number(p.orig_price ?? p.price ?? 0),
	         min_price: p.min_price == null ? null : Number(p.min_price),
	         qty: initQty, // 统一：购物车里永远存基本数量
	         product_image_url: p.product_image_url,
	         gtin: p.gtin,
	         aux_uom_name: p.aux_uom_name,
	         base_unit_name: p.base_unit_name,
	         aux_qty_in_base: p.aux_qty_in_base,
	         product_min_price: p.product_min_price == null ? null : Number(p.product_min_price),
	         max_discount: p.max_discount == null ? null : Number(p.max_discount),
	         available: Number(p.available || 0),
	         unitOptions: p.unitOptions,
	         selectedUnitIndex: p.selectedUnitIndex ?? 0,
	       })
	       return true
	},
	setEditingUpdatedAt(value){
		if (!this.editing_order_id) return false
		this.editing_updated_at = value || null
		return true
	},

	setQty(index, qty){ if(this.items[index]) this.items[index].qty = Math.max(0, Number(qty)||0) },
	remove(index){ this.items.splice(index,1) },
	clear(){ this.resetOrder() }
}
})

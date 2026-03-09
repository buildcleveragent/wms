import { defineStore } from 'pinia'

export const useCart = defineStore('cart', {
state: ()=>({
	owner_id: null,
	warehouse_id: null,
	customer: null, // {id, name}
	items: [] // [{product_id, sku, name, price, qty}]
}),
getters:{
	totalQty:(s)=> s.items.reduce((a,b)=> a + (b.qty||0), 0),
	totalAmount:(s)=> s.items.reduce((a,b)=> a + (b.qty||0)*(b.price||0), 0)
},
actions:{
	setContext({owner_id, warehouse_id}){ this.owner_id = owner_id; this.warehouse_id = warehouse_id },
	setCustomer(c){ this.customer = c },
	addItem(p){
		const exist = this.items.find(x=> x.product_id===p.id)
		if(exist){ exist.qty += 1; return }

		this.items.push({ 
			       product_id: p.id, sku:p.sku, 
				         name:p.name, 
			            price:Number(p.price||0), 
						  qty:1,
			product_image_url:p.product_image_url,
			             gtin:p.gtin,
				 aux_uom_name:p.aux_uom_name,
			   base_unit_name:p.base_unit_name,
			  aux_qty_in_base:p.aux_qty_in_base,		
	        product_min_price:Number(p.product_min_price||0),
			     max_discount:Number(p.max_discount||0),	
				    available:p.available,
				  unitOptions:p.unitOptions,				  
				  selectedUnitIndex: p.selectedUnitIndex,
			  })
		},

	setQty(index, qty){ if(this.items[index]) this.items[index].qty = Math.max(0, Number(qty)||0) },
	remove(index){ this.items.splice(index,1) },
	clear(){ this.items = [] }
}
})
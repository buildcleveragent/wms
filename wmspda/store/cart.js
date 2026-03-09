import { defineStore } from 'pinia'

export const useCart = defineStore('cart', 
{
	state: ()=>({owner: null,
	             supplier:null,
	             warehouse_id: null,	             
				 customer: null, // {id, name}
				 items: [] // [{product_id, sku, name, price, qty}]
				}),
	getters:{
		totalQty:(s)=> s.items.reduce((a,b)=> a + (b.base_quantity||0), 0),
		totalAmount:(s)=> s.items.reduce((a,b)=> a + (b.qty||0)*(b.price||0), 0)
	},
	actions:{
		setContext({owner_id, warehouse_id}){ this.owner_id = owner_id; this.warehouse_id = warehouse_id },
		setOwner(c){ this.owner = c },
		setSupplier(c){ this.supplier = c },
		setCustomer(c){ this.customer = c },
		addItem(p){
			const exist = this.items.find(x=> x.product_id===p.id)
			if(exist){ exist.qty += 1; return }

			this.items.push({ 
				                   id: p.id, 
				                   sku: p.sku, 
					              name: p.name, 
					               qty: 1,
					 product_image_url: p.product_image_url,
					              gtin: p.gtin,
					      aux_uom_name: p.aux_uom_name,
					    base_unit_name: p.base_unit_name,
		               aux_qty_in_base: p.aux_qty_in_base,		
					         packaging: p.packaging,
					       unitOptions: p.unitOptions,
					 selectedUnitIndex: p.selectedUnitIndex,
					     base_quantity: p.base_quantity,
				  })
			// console.log("cart base_quantity",base_quantity)	  
			},
		setQty(index, qty){ if(this.items[index]) this.items[index].qty = Math.max(0, Number(qty)||0) },
		
		setbase_quantity(index, base_quantity){ 
			console.log("setBase_quantity setBase_quantity")
			if(this.items[index]) {
				this.items[index].base_quantity = Math.max(0, Number(base_quantity)||0) 
				console.log("setBase_quantity this.items[index].base_quantity",this.items[index].base_quantity)
			  }
			else{
				console.log("setBase_quantity not this.items[index]",index,base_quantity)
			}
		},
		
		remove(index){ this.items.splice(index,1) },
		clear(){ this.items = [] }
	}
})
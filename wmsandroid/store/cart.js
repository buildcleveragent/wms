import { defineStore } from 'pinia'

function cleanText(v) {
	return String(v ?? '').trim()
}

function cleanLot(v) {
	return cleanText(v).toUpperCase()
}

function itemLotNo(it) {
	return cleanLot(it?.lot_no || it?.batch_number)
}

function itemMfgDate(it) {
	return cleanText(it?.mfg_date || it?.production_date)
}

function itemExpDate(it) {
	return cleanText(it?.exp_date || it?.expiry_date)
}

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
			const lotNo = itemLotNo(p)
			const mfgDate = itemMfgDate(p)
			const expDate = itemExpDate(p)
			const baseQty = Math.max(0, Number(p.base_quantity ?? p.qty ?? 1) || 0)
			const qty = Math.max(0, Number(p.qty ?? 1) || 0)

			const exist = this.items.find(x=>
				x.id === p.id &&
				itemLotNo(x) === lotNo &&
				itemMfgDate(x) === mfgDate &&
				itemExpDate(x) === expDate
			)
			if(exist){
				exist.qty = Math.max(0, Number(exist.qty || 0) + qty)
				exist.base_quantity = Math.max(0, Number(exist.base_quantity || 0) + baseQty)
				exist.lot_no = lotNo
				exist.batch_number = lotNo
				exist.mfg_date = mfgDate
				exist.production_date = mfgDate
				exist.exp_date = expDate
				exist.expiry_date = expDate
				return
			}

			this.items.push({ 
				                   id: p.id, 
				                   sku: p.sku, 
					              name: p.name, 
					               qty: qty,
								  spec: p.spec,
					 product_image_url: p.product_image_url,
					              gtin: p.gtin,
					      aux_uom_name: p.aux_uom_name,
					    base_unit_name: p.base_unit_name,
		               aux_qty_in_base: p.aux_qty_in_base,		
					         packaging: p.packaging,
					       unitOptions: p.unitOptions,
					 selectedUnitIndex: p.selectedUnitIndex,
					            lot_no: lotNo,
					    batch_number: lotNo,
					          mfg_date: mfgDate,
					  production_date: mfgDate,
					          exp_date: expDate,
					      expiry_date: expDate,
					     base_quantity: baseQty,
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
import { reactive } from 'vue'

export const inventoryCapabilities = () => ({
  can_view_owner_inventory: true, can_export_owner_inventory: true,
  can_manage_owner_inventory_alerts: true,
})
export function makeInventoryAuth() {
  return reactive({user:{id:1,owner_id:2}, roles:['owner_manager'], capabilities:inventoryCapabilities(),
    revokeInventoryAccess() {
      this.capabilities={can_view_owner_inventory:false,can_export_owner_inventory:false,can_manage_owner_inventory_alerts:false}
    },
  })
}

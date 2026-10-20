import { computed } from 'vue'
import { useAuth } from '@/store/auth'
import { ownerAccess } from '@/utils/ownerAccess'

export function useInventoryAccess() {
  const auth = useAuth()
  const access = computed(() => ownerAccess({roles: auth.roles, capabilities: auth.capabilities}))
  return {
    canView: computed(() => access.value.canViewInventory),
    canExport: computed(() => access.value.canExportInventory),
    canManage: computed(() => access.value.canManageInventoryAlerts),
    identity: computed(() => JSON.stringify([auth.user?.id, auth.user?.owner_id, auth.roles, auth.capabilities])),
    revoke: () => auth.revokeInventoryAccess(),
  }
}

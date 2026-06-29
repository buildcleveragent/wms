import { api } from '../utils/request'

export const authService = {
  login: (username, password) => api.login(username, password),
  wechatLogin: (payload) => api.saleMiniWechatLogin(payload),
  me: () => api.saleMiniMe(),
}

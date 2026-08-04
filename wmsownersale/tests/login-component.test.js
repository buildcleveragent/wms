import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ login: vi.fn() }))
vi.mock('@/store/auth', () => ({ useAuth: () => auth }))

import LoginPage from '@/pages/login.vue'

describe('登录页状态', () => {
  beforeEach(() => {
    auth.login.mockReset()
    vi.stubGlobal('uni', {
      showToast: vi.fn(),
      switchTab: vi.fn(),
    })
  })

  it('提交期间忽略重复点击', async () => {
    let resolveLogin
    auth.login.mockImplementation(() => new Promise((resolve) => { resolveLogin = resolve }))
    const wrapper = mount(LoginPage)

    await wrapper.find('button').trigger('click')
    await wrapper.find('button').trigger('click')

    expect(auth.login).toHaveBeenCalledOnce()
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
    resolveLogin({ id: 1 })
    await flushPromises()
  })

  it('429 显示服务端返回的剩余等待秒数', async () => {
    auth.login.mockRejectedValue({
      statusCode: 429,
      message: '登录尝试过于频繁，请稍后重试。',
      data: { retry_after: 37 },
    })
    const wrapper = mount(LoginPage)

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('请37秒后重试')
    expect(uni.showToast).toHaveBeenCalledWith({
      title: '登录尝试过于频繁，请37秒后重试',
      icon: 'none',
    })
    expect(uni.switchTab).not.toHaveBeenCalled()
  })
})

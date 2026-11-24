<script setup lang="ts">
/** RUN-010 WP1：登录页。真实后端认证，不做本地角色判定。 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Eye, EyeOff, LoaderCircle, ShieldCheck } from '@lucide/vue'
import { fetchAuthStatus, login } from '../auth'
import { ApiError } from '../api'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const showPassword = ref(false)
const busy = ref(false)
const error = ref('')
const authEnabled = ref(true)
const authChecked = ref(false)

const redirectTarget = computed(() => {
  const raw = route.query.redirect
  const value = Array.isArray(raw) ? String(raw[0]) : String(raw ?? '')
  return value.startsWith('/') && !value.startsWith('/login') ? value : '/'
})

onMounted(async () => {
  try {
    const status = await fetchAuthStatus()
    authEnabled.value = status.auth_enabled
  } catch {
    authEnabled.value = true // 查询失败按开启处理（fail-closed 展示）
  } finally {
    authChecked.value = true
  }
  // 认证开启但已有有效会话 → 直接进入
  try {
    const { loadMe } = await import('../auth')
    const user = await loadMe()
    if (user) void router.replace(redirectTarget.value)
  } catch {
    /* 未登录：停留登录页 */
  }
})

async function submit(): Promise<void> {
  if (busy.value) return
  error.value = ''
  if (!username.value.trim() || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  busy.value = true
  try {
    await login(username.value.trim(), password.value)
    await router.replace(redirectTarget.value)
  } catch (e) {
    if (e instanceof ApiError) {
      error.value =
        e.code === 'LOGIN_RATE_LIMITED'
          ? '尝试次数过多，请稍后再试'
          : e.code === 'INVALID_CREDENTIALS'
            ? '用户名或密码错误'
            : e.message || '登录失败'
    } else {
      error.value = e instanceof Error ? e.message : String(e)
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card" role="main">
      <div class="login-brand">
        <div class="brand-mark"><ShieldCheck :size="18" :stroke-width="2.2" /></div>
        <div>
          <div class="brand-name">TraceGuard</div>
          <div class="brand-sub">智能 Web UI 自动化测试与质量平台</div>
        </div>
      </div>

      <div v-if="!authChecked" class="login-hint"><LoaderCircle :size="14" class="spin-slow" /> 正在检查认证状态…</div>

      <template v-else>
        <div v-if="!authEnabled" class="login-warn">
          当前环境认证未开启（开发模式）。API 以匿名方式访问，仅限本地开发使用。
        </div>

        <form v-else class="login-form" @submit.prevent="submit">
          <div class="field">
            <label for="login-username">用户名</label>
            <input
              id="login-username"
              v-model="username"
              name="username"
              type="text"
              autocomplete="username"
              required
              placeholder="请输入用户名"
            />
          </div>
          <div class="field">
            <label for="login-password">密码</label>
            <div class="password-wrap">
              <input
                id="login-password"
                v-model="password"
                name="password"
                :type="showPassword ? 'text' : 'password'"
                autocomplete="current-password"
                required
                placeholder="请输入密码"
              />
              <button
                type="button"
                class="icon-btn password-toggle"
                :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                @click="showPassword = !showPassword"
              >
                <Eye v-if="!showPassword" :size="15" /><EyeOff v-else :size="15" />
              </button>
            </div>
          </div>

          <div v-if="error" class="login-error" role="alert">{{ error }}</div>

          <button class="primary login-submit" type="submit" :disabled="busy">
            <LoaderCircle v-if="busy" :size="15" class="spin-slow" />
            {{ busy ? '登录中…' : '登录' }}
          </button>
        </form>
      </template>
    </div>
    <p class="login-foot">身份由服务端会话与 RBAC 校验 · 前端仅为权限展示</p>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background:
    radial-gradient(1200px 500px at 20% -10%, var(--brand-weak), transparent 60%),
    var(--bg);
  padding: 24px;
}
.login-card {
  width: min(400px, 100%);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: var(--shadow);
  padding: 28px;
}
.login-brand { display: flex; gap: 12px; align-items: center; margin-bottom: 22px; }
.brand-mark {
  width: 38px; height: 38px; border-radius: 10px; display: grid; place-items: center;
  background: var(--brand-weak); color: var(--brand);
}
.brand-name { font-weight: 700; font-size: 16px; }
.brand-sub { font-size: 11.5px; color: var(--text-3); margin-top: 2px; }
.field { margin-bottom: 14px; }
.password-wrap { position: relative; }
.password-wrap input { width: 100%; padding-right: 36px; }
.password-toggle {
  position: absolute; right: 4px; top: 50%; transform: translateY(-50%);
  border: none; background: transparent;
}
.login-error {
  background: var(--fail-weak); color: var(--fail); border: 1px solid var(--fail-border);
  border-radius: 8px; padding: 8px 12px; font-size: 12.5px; margin-bottom: 14px;
}
.login-warn {
  background: var(--warn-weak); color: var(--warn); border: 1px solid var(--warn-border);
  border-radius: 8px; padding: 10px 12px; font-size: 12.5px;
}
.login-submit { width: 100%; justify-content: center; display: inline-flex; align-items: center; gap: 6px; }
.login-hint { color: var(--text-3); font-size: 12.5px; display: flex; gap: 6px; align-items: center; }
.login-foot { margin-top: 18px; text-align: center; color: var(--text-3); font-size: 11.5px; }
</style>

"""RUN-010 WP1：认证环境策略（启动期强制，不依赖开发者记得设置环境变量）。

策略矩阵（指令 §1.1）：

| 环境        | 认证要求                                          |
|-------------|---------------------------------------------------|
| development | 允许显式关闭，必须有醒目警告                       |
| test        | 允许隔离测试配置                                   |
| staging     | 默认必须开启                                       |
| production  | 默认必须开启，关闭时启动失败                        |

附加硬性要求：
- production：TG_AUTH_COOKIE_SECURE 必须开启（HTTPS 会话 Cookie）；
- production：session TTL 不得超过 24h（防长期悬挂会话）。

判定规则：environment 以 TG_ENVIRONMENT 显式配置为准；未显式设置 TG_AUTH_ENABLED
且 environment 为 staging/production 时按「默认开启」处理——这不是放宽，而是把
"开发者忘记设置" 从安全事故降级为正常路径。
"""

from __future__ import annotations

from dataclasses import dataclass

from traceguard_core.config import Settings

AUTH_REQUIRED_ENVIRONMENTS = {"staging", "production"}
AUTH_EXPLICIT_OFF_ALLOWED = {"development", "test", "local", "ci", ""}
MAX_PROD_SESSION_TTL_S = 24 * 3600


@dataclass(frozen=True)
class AuthPolicyDecision:
    ok: bool
    auth_effective: bool
    """应用启动时实际生效的认证开关（含「生产默认开启」推导）。"""
    fail_reason: str = ""
    warnings: list[str] | None = None

    @property
    def warnings_or_empty(self) -> list[str]:
        return list(self.warnings or [])


def _env_flag(env_key: str) -> bool | None:
    """读取原始环境变量布尔值（不经过 Settings 缓存，供启动校验用）。"""
    import os

    raw = os.environ.get(env_key)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def evaluate_auth_policy(settings: Settings) -> AuthPolicyDecision:
    env = (settings.environment or "").strip().lower()
    explicit_enabled = _env_flag("TG_AUTH_ENABLED")

    warnings: list[str] = []

    if env in AUTH_REQUIRED_ENVIRONMENTS:
        if explicit_enabled is False:
            # 显式关闭 + 受控环境：production 启动失败；staging 亦视为配置错误。
            return AuthPolicyDecision(
                ok=False,
                auth_effective=False,
                fail_reason=(
                    f"AUTH_DISABLED_IN_{env.upper()}: TG_AUTH_ENABLED=false is not allowed "
                    f"when TG_ENVIRONMENT={env}. Authentication is mandatory."
                ),
            )
        # 未显式设置时默认开启（fail-safe，不依赖开发者记得设置）。
        auth_effective = True
        if explicit_enabled is None:
            warnings.append(f"auth_enabled defaulted to true because TG_ENVIRONMENT={env}")
        if env == "production" and not settings.auth_cookie_secure:
            return AuthPolicyDecision(
                ok=False,
                auth_effective=auth_effective,
                fail_reason=(
                    "AUTH_COOKIE_INSECURE_IN_PRODUCTION: TG_AUTH_COOKIE_SECURE must be true "
                    "in production (HTTPS session cookie)."
                ),
            )
        if settings.auth_session_ttl_s > MAX_PROD_SESSION_TTL_S:
            return AuthPolicyDecision(
                ok=False,
                auth_effective=auth_effective,
                fail_reason=(
                    f"AUTH_SESSION_TTL_TOO_LONG: TG_AUTH_SESSION_TTL_S="
                    f"{settings.auth_session_ttl_s} exceeds the {MAX_PROD_SESSION_TTL_S}s "
                    "ceiling for controlled environments."
                ),
            )
        return AuthPolicyDecision(ok=True, auth_effective=auth_effective, warnings=warnings)

    # 非受控环境：显式关闭允许，但必须给出醒目警告。
    if explicit_enabled is False or not settings.auth_enabled:
        warnings.append(
            f"AUTH_DISABLED: authentication is OFF in environment='{env or 'local'}'. "
            "This is only acceptable for development/CI. All business APIs are exposed."
        )
        return AuthPolicyDecision(ok=True, auth_effective=False, warnings=warnings)
    return AuthPolicyDecision(ok=True, auth_effective=True, warnings=warnings)


def apply_auth_policy(settings: Settings) -> AuthPolicyDecision:
    """启动期入口：返回判定结果；不通过时由调用方决定抛错（production fail-closed）。"""
    return evaluate_auth_policy(settings)

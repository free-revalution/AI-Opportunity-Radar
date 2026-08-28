# Subscription & Activation — V2.0 商业化交付

> 用户购买流程: 闲鱼下单 → 获得 Activation Code → 飞书 `/activate` → 绑定
> Subscription → `/today` 立即可用。详见 ``docs/下一阶段开发技术方案.md``
> §44-53 / §88-92。

## 1. 套餐

| Plan | 价格(CNY/月) | 每日 Signal | 深度研究 | 自动生成内容 | 自动发布 |
|---|---|---|---|---|---|
| free | 0 | 1 | 0 | 0 | ✗ |
| basic | 29 | 5 | 1 | 3 | ✗ |
| pro | 59 | 20 | 5 | 10 | ✓ |
| creator | 129 | ∞ | ∞ | ∞ | ✓ |

价格不硬编码到 generator / publisher 逻辑,集中在 `app.services.subscriptions.PLAN_CATALOGUE`。
需要调整时改这一个 dict 即可(per docs §48)。

## 2. Activation Code 流程

### 2.1 生成

```python
from app.services.activation import issue_code

issued = issue_code("pro")
# issued.code         = "ABCD-EFGH-JKLM"     # 明文,仅 admin 看到一次
# issued.code_hash    = "64 位 sha256 十六进制"  # 持久化到 DB
# issued.plan         = "pro"
# issued.expires_at   = datetime (默认 +365 天)
```

**明文只显示一次,服务端只存 hash**。盐值在 `DEFAULT_SERVER_PEPPER`,
生产环境通过 `APP_SECRET_KEY` 注入。

### 2.2 兑换

```python
from app.services.activation import redeem_code, ActivationError

def lookup_by_hash(code_hash: str):
    return db.query(ActivationCode).filter_by(code_hash=code_hash).first()

outcome = redeem_code(
    code="ABCD-EFGH-JKLM",
    feishu_open_id="ou_user_xxx",
    lookup_by_hash=lookup_by_hash,
)

if not outcome.success:
    if outcome.error == ActivationError.NOT_FOUND:
        return "激活码无效"
    if outcome.error == ActivationError.ALREADY_BOUND:
        return "该激活码已被其他账号使用"
    if outcome.error == ActivationError.EXPIRED:
        return "激活码已过期"
    if outcome.error == ActivationError.REVOKED:
        return "激活码已被吊销"
```

### 2.3 防止分享

每个 Code 只能绑定一个 Feishu Open ID:

- 同一 Open ID 重复绑定 → 幂等成功
- 同一 Code 绑定不同 Open ID → `ALREADY_BOUND`

异常(同一 Open ID 绑定多个 Code)在 admin 面板可见(Phase 13 admin API)。

## 3. Subscription Gating

所有付费功能路径都必须经过 `gate()`:

```python
from app.services.subscriptions import gate

verdict = gate(
    subscription=user.active_subscription,
    feature="research",
)
if not verdict.allowed:
    return f"该功能需要升级到 {verdict.upgrade_to}"
```

Recognised features:

- `view_top_signals` — /today, /top
- `research`         — /research <id>
- `content_full`     — /content <id> 返回完整输出
- `auto_publish`     — publisher 可自动发布

## 4. 过期行为

SubscriptionService 检查:

1. `status == "active"`
2. `expires_at > now` (含 grace period)

过期后:

- /today → 仅返回基础 Signal(Free 配额)
- /research / /content → "订阅已到期,基础免费内容仍可使用,如需继续请续订"
- /help / /preferences / 订阅续费信息 → 仍可用

## 5. Audit Trail

每次 publish / research / content_generate / activate 都写一行
`AuditLog`(`actor_type=system | admin | bot, action=*, result=success/failure/blocked`)。
具体 helpers:

- `AuditService.record_publish(...)`
- `AuditService.record_rbac_deny(...)`
- `AuditService.record_compliance_block(...)`

Admin 面板提供"最近活动"feed,Phase 13 接 UI。

## 6. 数据模型

```
users
   ↓
subscriptions          (1 user → N subscriptions, history)
   ↓
activation_codes       (admin-issued, code_hash stored)
   ↓
audit_logs             (append-only, every action)
```

`ActivationCode.bound_feishu_open_id` 字段让"Xianyu 卖 → 飞书绑定"流程
不需要用户先注册 email — 飞书 Open ID 直接是主键。

## 7. 限流

`ActivationError.RATE_LIMITED` 已定义但尚未在 v2.0 中实现。Phase 13
加入 Redis-based 限流:同一用户/Feishu ID 每 10 分钟最多 5 次失败尝试,
超过则临时封锁。

## 8. 测试

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_phase12e_services.py::TestActivationCode` | 15 用例 |
| `tests/test_phase12e_services.py::TestSubscriptionGating` | 15 用例 |
| `tests/test_phase12e_services.py::TestAuditService` | 9 用例 |

共 39 个用例,覆盖:

- 格式校验 / hash 生成 / pepper 校验
- 兑换 happy path + 5 种失败模式
- Subscription gating 各种 plan × feature 组合
- Plan 定价与文档对齐(¥0/¥29/¥59/¥129)
- Audit ring buffer 行为 + JSON 序列化

## 9. 下一阶段

- Phase 13: Admin API(`/api/admin/subscriptions` `/api/admin/activation`) —
  用 FastAPI 暴露 CRUD。
- Phase 14: 与 `feishu/rbac.py` 接通 — `/activate <code>` 路径。
- Phase 15: 续费流程(到期前 7 天 Feishu 推送提醒)。
- Phase 16: Payment 自动化(目前是闲鱼人工 → 半自动)。
# experimental/

MVP 阶段冻结的代码与文档（per 简化方案 §6）。

## 目录约定

- `backend/` — 冻结的 Python 后端代码（services / repositories / api / tests）
- `frontend/` — 整个前端项目（MVP 阶段暂停，simplify §49）
- `n8n/workflows/` — 冻结的 n8n workflow JSON
- `docs/` — 已废止的旧文档

## 恢复方式

被冻结的代码**未被删除**——如需恢复，从对应子目录移回原位：

```bash
# 示例：恢复 activation 服务
mv experimental/backend/app/services/activation backend/app/services/
```

代码可能依赖 freezed-only 的其他模块，移回前请检查 import 链。

## 冻结清单

见仓库根目录 `MVP_REFACTOR_PLAN.md` §3。

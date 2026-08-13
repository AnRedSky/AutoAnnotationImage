# 数据库迁移脚本 (backend/migrations)

> 规范化编号 + 严格升序执行 + 完整幂等的数据库变更体系

---

## 一、目录结构

```
backend/migrations/
├── _registry.py           # 迁移清单 (按 order 升序声明, 单一真实来源)
├── runner.py              # 执行器 (按 _registry 升序执行, 失败即停)
├── 00_add_detection_segmentation.py
├── 01_extend_annotation_log_enum.py
├── 02_add_training_log.py
├── 03_add_training_created_at.py
├── 04_add_training_device_info.py
├── 05_add_training_pretrain_mode.py
├── 06_backfill_pretrain_mode_finetune.py
├── 07_add_team_enhance.py
├── 08_add_team_l5_indexes.py
├── 09_enforce_model_dataset_notnull.py
├── 10_add_training_t7_indexes.py
└── README.md              # 本文件
```

## 二、编号与执行规则 (硬性约束)

### 2.1 文件命名

- **格式**: `NN_name.py`, NN 为两位数字 (`00` ~ `99`)
- **数字部分**: 严格升序, 不可重复, 必须从 `00` 开始
- **命名**: 蛇形小写字母 + 数字 + 下划线, 描述性强 (e.g. `add_training_log.py`)
- **不允许**: 单数字 (`0_`)、字母前缀 (`v1_`)、三位数字 (`100_`)

### 2.2 执行顺序

runner.py 启动时按 `_registry.MIGRATIONS` 列表的 `order` 字段升序执行, 严格保证:

- 后一个脚本可依赖前一个脚本已建的列/表/索引
- 失败立即终止, 防止部分迁移导致数据库结构异常
- 全部幂等, 重复执行安全 (基于 `information_schema` 判定)

### 2.3 新增迁移的标准流程

1. **取号**: 当前最大序号 + 1 (查看 `python -m migrations.runner --list`)
2. **创建文件**: `NN_descriptive_name.py`, 顶部声明 `MIGRATION_ID` 和 `MIGRATION_DESCRIPTION`
3. **暴露入口**: 异步函数 `async def run_migration() -> None`
4. **注册**: 在 `_registry.MIGRATIONS` 列表末尾追加 `Migration(order, name, description, module)`
5. **校验**: `python -m migrations.runner --check` 确认 _registry 与磁盘文件一致
6. **测试**: 在 `tests/` 加 smoke test, 验证 `run_migration()` 幂等

### 2.4 严禁行为

- 修改现有迁移的 `order` (会破坏已部署环境)
- 在迁移中执行 `DROP TABLE` / `DROP COLUMN` (请走废弃流程 + 独立版本)
- 在迁移中耦合业务逻辑 (迁移应只做表结构 + 数据回填)
- 跨序号间隔 (e.g. 03 之后直接 05, 除非有特殊原因并在 PR 描述中说明)
- 写死当前时间戳 / 用户名 / 路径 (迁移应可在任意环境运行)

## 三、迁移清单 (按 order 升序)

| order | 名称 | 描述 | 依赖 |
|---|---|---|---|
| 00 | add_detection_segmentation | v2.0.0: 新建 bbox_annotation/segmentation_mask 表; image/model_version/training_jobs 加 task_type; model_version 加 5 个任务指标 | 无 (必须最早) |
| 01 | extend_annotation_log_enum | v2.5.15 P0-2: annotation_log 加 payload JSON, action ENUM 扩展 (auto_annotate_pretrained/auto_annotate_finetuned) | 00 |
| 02 | add_training_log | training_jobs 加 log JSON 字段, 存训练日志行 | 00 |
| 03 | add_training_created_at | training_jobs 加 created_at DATETIME 字段, 历史行回填, 加索引 | 02 |
| 04 | add_training_device_info | training_jobs 加训练资源字段 (device_type/device_name/device_info/gpu_peak_memory_mb) | 02 |
| 05 | add_training_pretrain_mode | training_jobs 加 pretrain_mode/pretrain_source_mv_id 字段, 加 pretrain_mode 索引 | 02, 04 |
| 06 | backfill_pretrain_mode_finetune | 回填历史 training_jobs.pretrain_mode = 'from_scratch' (NULL → from_scratch) | 05 (强依赖) |
| 07 | add_team_enhance | v3.3.1: team.tenant_id/archived_at, team_member.invited_by_id, annotation_log.team_id; 3 个新索引; 回填 annotation_log.team_id | 00 |
| 08 | add_team_l5_indexes | v3.3.1 L5: 团队管理复合索引 (audit_log team_created/event_created/user_created/resource) | 07 |
| 09 | enforce_model_dataset_notnull | v3.3.5: model_version.dataset_id 强制 NOT NULL; 历史孤儿 model 关联到 _system_orphan_models_holder dataset | 00 |
| 10 | add_training_t7_indexes | v3.5.0 Phase T7: training_jobs 索引优化 (base_model/model_name/user_type/dataset) | 02/03/04/05 |

## 四、使用方式

### 4.1 启动时自动执行

应用启动时 (lifespan hook) 自动跑全部迁移, 无需手动操作:

```python
# app/main.py lifespan
async def lifespan(app: FastAPI):
    from migrations.runner import run_all_migrations
    await run_all_migrations()  # 严格按 order 升序
    ...
```

### 4.2 手动 CLI 触发

```bash
# 列出所有迁移 (不执行)
python -m migrations.runner --list

# 校验 _registry 与磁盘文件一致性
python -m migrations.runner --check

# 执行全部迁移 (默认从 00 开始)
python -m migrations.runner

# 只执行指定 order
python -m migrations.runner --only 03

# 从指定 order 开始 (含) 一直跑到末尾
python -m migrations.runner --from 05

# 失败不停止 (默认失败会立即停止, 安全策略)
python -m migrations.runner --no-stop

# 详细日志
python -m migrations.runner -v
```

### 4.3 单脚本独立执行 (兼容历史用法)

```bash
# 直接跑某条迁移 (用于本地调试)
python migrations/03_add_training_created_at.py
```

每个迁移文件底部保留 `async def main()` 入口, 兼容直接调用。

## 五、迁移脚本模板

```python
"""
迁移脚本 NN: <一句话功能描述>
=========================================
**MIGRATION_ID**: NN
**功能**:
- <改动 1>
- <改动 2>
**幂等**: 重复执行安全 (<判定方式>)
**执行顺序**: NN, 依赖 <上游 order>
"""
import asyncio
from app.database import engine
from sqlalchemy import text


MIGRATION_ID = "NN"
MIGRATION_DESCRIPTION = "<一句话描述, 用于 _registry>"


async def run_migration() -> None:
    """runner 入口, 必须是 async 函数"""
    async with engine.begin() as conn:
        # 实现迁移逻辑
        ...
    print(f"[done] [{MIGRATION_ID}] {MIGRATION_DESCRIPTION}")


async def main():
    """兼容历史 CLI 调用 (python migrations/NN_xxx.py)"""
    await run_migration()


if __name__ == "__main__":
    asyncio.run(main())
```

## 六、幂等性要求 (强制)

每个迁移必须满足:

1. **列添加幂等**: 用 `information_schema.COLUMNS` 检查列存在, 存在则跳过
2. **索引创建幂等**: 用 `information_schema.STATISTICS` 检查索引存在, 存在则跳过
3. **数据回填幂等**: 用 `WHERE column IS NULL` 限定范围, 重复执行 0 行更新
4. **约束收紧幂等**: 用 `SHOW COLUMNS` 检查当前约束, 已收紧则跳过
5. **副作用隔离**: 单条迁移失败不影响其他迁移 (runner 默认失败即停, 但脚本本身要干净)

## 七、CI / 部署检查清单

- [ ] 所有迁移文件名匹配 `NN_name.py` 格式
- [ ] `_registry.MIGRATIONS` 列表与磁盘文件一一对应 (`--check` 通过)
- [ ] 所有迁移有 `MIGRATION_ID` 常量, 与文件名一致
- [ ] 所有迁移暴露 `async def run_migration()`
- [ ] 新增迁移有对应 smoke test (放在 `tests/test_migration_*.py`)
- [ ] PR 描述中明确依赖关系 (上游 order)
- [ ] 跨 DB 兼容: MySQL 5.7+ / SQLite (CREATE INDEX 重复错误静默吞)
- [ ] 失败日志清晰 (包含 ORDER / 错误类型 / 上下文)

## 八、相关文档

- 启动时自动迁移: `app/main.py` lifespan
- Alembic (本项目保留为可选): 见 [使用手册 § 3.2.1](../../docs/使用手册.md)
- 历史迁移报告: `docs/archive/` (按版本号归档)

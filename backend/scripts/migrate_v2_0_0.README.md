# v2.0.0 数据库迁移指南

## 问题
从 v1.0.0 升级到 v2.0.0 后, MySQL 报错:
```
pymysql.err.OperationalError: (1054, "Unknown column 'training_jobs.task_type' in 'field list'")
```

## 根因
v2.0.0 在以下 3 张已有表加了新列, 但项目用 `init_db()` (即 `Base.metadata.create_all()`) 只**建新表不补列**:
- `training_jobs.task_type`
- `model_version.task_type` + 5 个 FLOAT 指标列 (map_50 / map_50_95 / miou / pixel_accuracy / dice_score)
- `image.task_type`

## 解决方案 (二选一)

### 方案 A: MySQL 直接 SQL (推荐, 无 Python 依赖)

```bash
# 在 backend/scripts/ 目录下
mysql -u root -p your_database < migrate_v2_0_0.sql
```

或者登录 MySQL 客户端:
```sql
USE your_database;
SOURCE backend/scripts/migrate_v2_0_0.sql;
CALL migrate_v2_0_0();
```

存储过程会先检查列/表是否存在, 重复执行不会报错.

### 方案 B: Python 脚本 (跨 MySQL+SQLite)

```bash
cd backend
python scripts/migrate_v2_0_0.py
```

脚本会自动读 `app.config.settings.EFFECTIVE_DATABASE_URL` (来自 .env / 环境变量).

## 验证

迁移后再次访问 `/api/training/jobs/` 不再 500, 应返回正常 JSON.

可用以下 SQL 验证:
```sql
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND ((TABLE_NAME = 'training_jobs' AND column_name = 'task_type')
    OR (TABLE_NAME = 'model_version' AND column_name IN ('task_type', 'map_50', 'map_50_95', 'miou', 'pixel_accuracy', 'dice_score'))
    OR (TABLE_NAME = 'image' AND column_name = 'task_type'))
ORDER BY TABLE_NAME, column_name;
```

应返回 8 行.

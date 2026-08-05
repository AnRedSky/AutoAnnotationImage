# 性能基准测试 (v3.3.1 L5)

团队管理模块的性能 SLA 验证.

## 测试目标

| 端点 | SLA (P95) | 备注 |
|---|---|---|
| GET /api/teams (列表分页) | < 200ms | 含分页 + 搜索 + 排序 |
| GET /api/teams/{id} (详情) | < 50ms | Redis 缓存命中场景 |
| GET /api/stats/team/{id} (统计) | < 500ms | 聚合查询 |
| GET /api/teams/{id}/activities | < 300ms | 审计聚合 |
| GET /api/audit-logs (admin) | < 200ms | 分页 + 过滤 |

## 前置条件

1. **后端服务运行**: `uv run api` 或 `python -m app.main`
2. **测试用户**:
   - `perf_admin` (admin 角色)
   - `perf_user` (annotator 角色)
3. **测试数据**:
   - 至少 5 个团队 (用户需加入)
   - 至少 1 个有数据集/标注记录的团队 (用于统计测试)

可通过环境变量配置:
```bash
export PERF_ADMIN_USER=perf_admin
export PERF_ADMIN_PASS=perftest123
export PERF_USER=perf_user
export PERF_PASS=perftest123
```

## 运行方式

### 1. Web UI 模式 (推荐调试)

```bash
cd backend
locust -f tests/load/locust_team.py --host=http://localhost:8000
```

打开 http://localhost:8089, 设置用户数和持续时间.

### 2. 无头模式 (CI/CD 友好)

```bash
cd backend
python tests/load/run_benchmark.py --users 50 --spawn 10 --duration 60
```

### 3. 直接调用 locust

```bash
locust -f tests/load/locust_team.py --host=http://localhost:8000 \
  --headless -u 100 -r 20 -t 120s \
  --csv=perf-results/perf --html=perf-results/report.html
```

## 输出文件

- `perf-results/perf-{timestamp}_stats.csv` - 端点统计
- `perf-results/perf-{timestamp}_stats_history.csv` - 时间序列
- `perf-results/perf-{timestamp}.html` - HTML 报告
- `perf-results/perf-{timestamp}_failures.csv` - 失败记录

## SLA 检查

`locust_team.py` 末尾注册了 `test_stop` 事件, 测试结束后自动打印 SLA 检查报告:

```
============================================================
SLA 检查报告
============================================================
  ✅ [team] list_teams: P95=180ms (阈值 200ms)
  ✅ [team] get_detail (cached): P95=12ms (阈值 50ms)
  ✅ [team] get_stats: P95=420ms (阈值 500ms)
  ...
============================================================
```

## 测试场景

### TeamBenchmarkUser (普通用户)
- 5x 列表分页 (含排序)
- 3x 团队详情 (重复访问触发缓存)
- 2x 成员列表
- 1x 数据集列表
- 1x 活动 Feed
- 1x 统计聚合

### AdminBenchmarkUser (管理员)
- 5x 审计日志分页
- 2x 缓存统计
- 1x 审计日志过滤查询

## 性能调优建议

如果 SLA 不达标, 检查以下项:
1. **数据库索引**: team_id / created_at / user_id 索引
2. **Redis 缓存**: 缓存命中率应 > 80%
3. **N+1 查询**: 检查 ORM 关系加载
4. **审计日志膨胀**: 考虑按月分区或归档冷数据

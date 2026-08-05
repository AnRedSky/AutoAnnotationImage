"""
Locust 性能基准测试 (v3.3.1 L5)
================================

测试目标: 验证团队管理模块的响应时间 SLA
  - 列表分页 P95 < 200ms
  - 详情缓存命中 P95 < 50ms
  - 统计聚合 P95 < 500ms

运行方式:
  # 单机模式 (web UI)
  locust -f tests/load/locust_team.py --host=http://localhost:8000

  # 无头模式
  locust -f tests/load/locust_team.py --host=http://localhost:8000 \\
    --headless -u 50 -r 10 -t 60s

  # 自定义用户数 + 持续时间
  locust -f tests/load/locust_team.py --host=http://localhost:8000 \\
    --headless -u 100 -r 20 -t 120s --csv=perf-results

前置条件:
  - 至少有 1 个 admin + 1 个普通用户
  - 至少有 5 个团队 (用于列表分页测试)
  - 至少有 1 个有数据的团队 (用于统计聚合测试)
"""
import os
import random
import time
from locust import HttpUser, task, between, events


# 测试用登录凭据 (需要在 .env 或环境变量中配置)
ADMIN_USER = os.getenv("PERF_ADMIN_USER", "perf_admin")
ADMIN_PASS = os.getenv("PERF_ADMIN_PASS", "perftest123")
NORMAL_USER = os.getenv("PERF_USER", "perf_user")
NORMAL_PASS = os.getenv("PERF_PASS", "perftest123")


class TeamBenchmarkUser(HttpUser):
    """团队管理性能基准用户 (登录普通用户, 模拟浏览场景)."""

    wait_time = between(0.5, 2.0)  # 思考时间 0.5-2s

    def on_start(self):
        """登录获取 token."""
        resp = self.client.post(
            "/api/auth/login",
            data={"username": NORMAL_USER, "password": NORMAL_PASS},
            name="[auth] login",
        )
        if resp.status_code == 200:
            self.token = resp.json().get("access_token")
            self.headers = {"Authorization": f"Bearer {self.token}"}
        else:
            self.token = None
            self.headers = {}
            print(f"[ERROR] login failed: {resp.status_code} {resp.text}")

        # 拉取我的团队列表 (用于后续任务的 team_id 池)
        if self.token:
            self._refresh_team_pool()

    def _refresh_team_pool(self):
        """从 /api/teams 拉取可见团队 ID 池."""
        try:
            resp = self.client.get(
                "/api/teams?page=1&page_size=50",
                headers=self.headers,
                name="[init] list_teams",
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                self.team_ids = [t["id"] for t in items]
            else:
                self.team_ids = []
        except Exception as e:
            print(f"[ERROR] refresh team pool: {e}")
            self.team_ids = []

    @task(5)
    def list_my_teams(self):
        """高频任务: 列出我的团队 (P95 < 200ms)."""
        page = random.randint(1, 3)
        sort_options = [
            "id_desc", "name_asc", "name_desc",
            "member_count_desc", "created_desc",
        ]
        params = {
            "page": page,
            "page_size": 20,
            "sort": random.choice(sort_options),
        }
        self.client.get(
            "/api/teams",
            params=params,
            headers=self.headers,
            name="[team] list_teams",
        )

    @task(3)
    def get_team_detail(self):
        """高频任务: 团队详情 (P95 < 50ms 缓存命中)."""
        if not self.team_ids:
            return
        team_id = random.choice(self.team_ids)
        # 重复访问同 ID 触发 Redis 缓存命中
        self.client.get(
            f"/api/teams/{team_id}",
            headers=self.headers,
            name="[team] get_detail (cached)",
        )

    @task(2)
    def get_team_members(self):
        """团队成员列表."""
        if not self.team_ids:
            return
        team_id = random.choice(self.team_ids)
        self.client.get(
            f"/api/teams/{team_id}/members",
            headers=self.headers,
            name="[team] list_members",
        )

    @task(1)
    def get_team_datasets(self):
        """团队级数据集列表."""
        if not self.team_ids:
            return
        team_id = random.choice(self.team_ids)
        self.client.get(
            f"/api/teams/{team_id}/datasets",
            headers=self.headers,
            name="[team] list_datasets",
        )

    @task(1)
    def get_team_activities(self):
        """团队活动 Feed (L4 新增)."""
        if not self.team_ids:
            return
        team_id = random.choice(self.team_ids)
        self.client.get(
            f"/api/teams/{team_id}/activities?limit=50",
            headers=self.headers,
            name="[team] list_activities",
        )

    @task(1)
    def get_team_stats(self):
        """团队统计聚合 (P95 < 500ms)."""
        if not self.team_ids:
            return
        team_id = random.choice(self.team_ids)
        days = random.choice([7, 14, 30])
        self.client.get(
            f"/api/stats/team/{team_id}?days={days}",
            headers=self.headers,
            name="[team] get_stats",
        )


class AdminBenchmarkUser(HttpUser):
    """管理员性能基准用户 (审计查询 + 缓存监控)."""

    wait_time = between(0.5, 2.0)

    def on_start(self):
        resp = self.client.post(
            "/api/auth/login",
            data={"username": ADMIN_USER, "password": ADMIN_PASS},
            name="[auth] admin_login",
        )
        if resp.status_code == 200:
            self.token = resp.json().get("access_token")
            self.headers = {"Authorization": f"Bearer {self.token}"}
        else:
            self.token = None
            self.headers = {}

    @task(5)
    def list_audit_logs(self):
        """审计日志分页查询."""
        page = random.randint(1, 5)
        self.client.get(
            f"/api/audit-logs?page={page}&page_size=50",
            headers=self.headers,
            name="[audit] list_logs",
        )

    @task(2)
    def get_cache_stats(self):
        """缓存统计."""
        self.client.get(
            "/api/teams/_cache/stats",
            headers=self.headers,
            name="[cache] stats",
        )

    @task(1)
    def audit_filtered(self):
        """审计日志按事件类型过滤."""
        event_types = [
            "team_created", "team_updated", "team_member_invited",
            "team_member_role_changed", "team_deleted", "team_restored",
            "dataset_shared_to_team",
        ]
        self.client.get(
            f"/api/audit-logs?event_type={random.choice(event_types)}",
            headers=self.headers,
            name="[audit] filtered",
        )


# SLA 报告 (Locust 事件钩子)
SLA_THRESHOLDS = {
    "[team] list_teams": 200,            # ms (P95)
    "[team] get_detail (cached)": 50,    # ms (P95, 缓存命中)
    "[team] get_stats": 500,             # ms (P95)
    "[team] list_activities": 300,       # ms (P95)
    "[audit] list_logs": 200,            # ms (P95)
}


@events.test_stop.add_listener
def check_sla(environment, **kwargs):
    """测试结束后检查 SLA."""
    print("\n" + "=" * 60)
    print("SLA 检查报告")
    print("=" * 60)
    stats = environment.stats
    failed = False
    for name, threshold in SLA_THRESHOLDS.items():
        entry = stats.get(name, "GET")
        if entry is None:
            print(f"  ⚠ {name}: 无数据")
            continue
        p95 = entry.get_response_time_percentile(0.95)
        status = "✅" if p95 <= threshold else "❌"
        if p95 > threshold:
            failed = True
        print(f"  {status} {name}: P95={p95:.0f}ms (阈值 {threshold}ms)")
    print("=" * 60)
    if failed:
        print("❌ SLA 不达标, 需优化")
    else:
        print("✅ SLA 全部达标")

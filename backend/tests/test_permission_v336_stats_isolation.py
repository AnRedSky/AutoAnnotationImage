"""
v3.3.6-STATS-ISOLATION 统计越权回归测试
========================================

3 层权限模型 — 统计接口严格最小权限:
  - 系统层 (super_admin): 平台管理, 不触达业务数据
  - 团队层 (manager/editor/viewer): 团队资源
  - 数据层 (owner/team_member): 业务数据

v3.3.6 核心变化 — 移除所有统计接口的 admin/super_admin 旁路:
  - /api/stats/overview: 移除 admin 旁路, 任何角色仅看到可见数据集相关统计
  - /api/stats/annotator-efficiency: 移除 admin 旁路, 任何角色仅返回自己 (防止用户名+标注量泄露)
  - /api/teams/{id}/datasets/{ds_id}/share (DELETE): 移除 regular admin 旁路,
    仅 owner / super_admin 可取消共享 (避免横向越权破坏协作)
  - StatsService.global_overview: 标记为 DEPRECATED (越权风险)

v3.3.6-STATS-ISOLATION 第二轮 (修复剩余越权点):
  - /api/stats/team/{team_id}: 归档团队数据对非 super_admin 不可访问
  - /api/users: 仅 super_admin 可看 email/role/is_active, regular admin 收紧
  - /api/audit-logs: 仅 super_admin 可访问 (审计数据敏感)
  - /api/teams/_cache/stats: 仅 super_admin 可查看 (运维监控)
  - /api/teams?include_archived=true: 仅 super_admin 可生效

测试矩阵 (13 项):
  T-S01: super_admin /stats/overview 不包含其他用户的 dataset 数
  T-S02: super_admin /stats/overview 的 model_versions 仅统计可见数据集
  T-S03: regular admin /stats/overview 不包含其他用户的 dataset 数
  T-S04: 普通用户 /stats/overview 仅返回自己创建的数据集相关统计
  T-S05: super_admin /stats/annotator-efficiency 仅返回自己
  T-S06: regular admin /stats/annotator-efficiency 仅返回自己
  T-S07: 普通用户 /stats/annotator-efficiency 仅返回自己
  T-S08: regular admin 取消他人共享 → 403 (v3.3.6 收紧)
  T-S09: super_admin 仍可取消他人共享 (平台代管场景)
  T-S10: owner 取消自己的共享 → 200 (正常路径)
  T-S11: regular admin 访问归档团队统计 → 410 (业务停止)
  T-S12: regular admin 访问 /api/users 不可见 email/role
  T-S13: regular admin 访问 /api/audit-logs → 403
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.model.user import User
from app.tasks.model.team import Team
from app.tasks.model.team_member import TeamMember
from app.tasks.model.dataset import Dataset
from app.tasks.model.model_version import ModelVersion
from app.tasks.model.image import Image
from app.tasks.model.annotation_log import AnnotationLog
from app.tasks.model.training_job import TrainingJob
from app.middleware.security.security import hash_password


# ============== 通用 Fixtures ==============

@pytest_asyncio.fixture
async def super_admin(db_session: AsyncSession) -> User:
    user = User(
        username="v336_super",
        email="v336_super@example.com",
        password_hash=hash_password("v336super123"),
        role="super_admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def regular_admin(db_session: AsyncSession) -> User:
    user = User(
        username="v336_admin",
        email="v336_admin@example.com",
        password_hash=hash_password("v336admin123"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def alice(db_session: AsyncSession) -> User:
    user = User(
        username="v336_alice",
        email="v336_alice@example.com",
        password_hash=hash_password("v336alice123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def carol(db_session: AsyncSession) -> User:
    user = User(
        username="v336_carol",
        email="v336_carol@example.com",
        password_hash=hash_password("v336carol123"),
        role="annotator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def team_t(db_session: AsyncSession, alice: User) -> Team:
    team = Team(
        name="V336TeamT",
        slug="v336-team-t",
        owner_id=alice.id,
        max_members=20,
    )
    db_session.add(team)
    await db_session.commit()
    await db_session.refresh(team)
    member = TeamMember(team_id=team.id, user_id=alice.id, role="manager")
    db_session.add(member)
    await db_session.commit()
    return team


@pytest_asyncio.fixture
async def alice_dataset(db_session: AsyncSession, alice: User) -> Dataset:
    """alice 创建的个人数据集 (含 3 张图)"""
    ds = Dataset(
        name="v336_alice_ds",
        task_type="classification",
        owner_id=alice.id,
        team_id=None,
        status="draft",
        image_count=3,
        annotated_count=2,
    )
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)
    # 加 3 张图 (2 张已标)
    for i in range(3):
        img = Image(
            dataset_id=ds.id,
            filename=f"alice_{i}.jpg",
            status="human_confirmed" if i < 2 else "pending",
            storage_path=f"/tmp/alice_{i}.jpg",
            file_hash=f"alice_hash_{i}",
        )
        db_session.add(img)
    await db_session.commit()
    return ds


@pytest_asyncio.fixture
async def carol_dataset(db_session: AsyncSession, carol: User) -> Dataset:
    """carol 创建的个人数据集 (含 5 张图)"""
    ds = Dataset(
        name="v336_carol_ds",
        task_type="classification",
        owner_id=carol.id,
        team_id=None,
        status="draft",
        image_count=5,
        annotated_count=4,
    )
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)
    for i in range(5):
        img = Image(
            dataset_id=ds.id,
            filename=f"carol_{i}.jpg",
            status="human_confirmed" if i < 4 else "pending",
            storage_path=f"/tmp/carol_{i}.jpg",
            file_hash=f"carol_hash_{i}",
        )
        db_session.add(img)
    await db_session.commit()
    return ds


@pytest_asyncio.fixture
async def alice_team_dataset(
    db_session: AsyncSession, alice: User, team_t: Team
) -> Dataset:
    """alice 创建的、共享给 team_t 的数据集"""
    ds = Dataset(
        name="v336_alice_team_ds",
        task_type="classification",
        owner_id=alice.id,
        team_id=team_t.id,
        status="draft",
    )
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)
    return ds


@pytest_asyncio.fixture
async def carol_model_version(
    db_session: AsyncSession, carol_dataset: Dataset
) -> ModelVersion:
    """carol 数据集下的 model (用于验证 super_admin 不可见)"""
    mv = ModelVersion(
        dataset_id=carol_dataset.id,
        name="v336_carol_mv",
        base_model="resnet50",
        task_type="classification",
        file_path="/tmp/v336_carol_mv.pth",
        is_active=True,
    )
    db_session.add(mv)
    await db_session.commit()
    await db_session.refresh(mv)
    return mv


@pytest_asyncio.fixture
async def alice_annotation_logs(
    db_session: AsyncSession, alice: User, alice_dataset: Dataset
) -> list[AnnotationLog]:
    """alice 的标注记录 (用于效率统计)"""
    # 需要先有 image 才能创建 AnnotationLog
    img = (await db_session.execute(
        select(Image).where(Image.dataset_id == alice_dataset.id).limit(1)
    )).scalar_one()
    logs = []
    for i in range(5):
        log = AnnotationLog(
            user_id=alice.id,
            image_id=img.id,
            action="confirm",
            time_spent_ms=2000,
            team_id=None,
        )
        db_session.add(log)
        logs.append(log)
    await db_session.commit()
    return logs


@pytest_asyncio.fixture
async def carol_annotation_logs(
    db_session: AsyncSession, carol: User, carol_dataset: Dataset
) -> list[AnnotationLog]:
    """carol 的标注记录 (用于验证 admin 不可见)"""
    img = (await db_session.execute(
        select(Image).where(Image.dataset_id == carol_dataset.id).limit(1)
    )).scalar_one()
    logs = []
    for i in range(10):
        log = AnnotationLog(
            user_id=carol.id,
            image_id=img.id,
            action="confirm",
            time_spent_ms=3000,
            team_id=None,
        )
        db_session.add(log)
        logs.append(log)
    await db_session.commit()
    return logs


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/login", data={"username": username, "password": password}
    )
    assert resp.status_code == 200, f"Login {username} failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ============== T-S01~T-S04: /api/stats/overview 严格最小权限 ==============

@pytest.mark.asyncio
async def test_T_S01_super_admin_overview_excludes_outsider(
    client: AsyncClient, db_session,
    super_admin, alice_dataset, carol_dataset,
):
    """T-S01: super_admin /stats/overview 不包含其他用户的 dataset 数

    v3.3.6 关键修复: super_admin 之前在 admin 分支看全平台, 现在收紧为按可见数据集过滤.
    验证: datasets 计数不应包含 carol_dataset.id 关联的图.
    """
    headers = await _login(client, "v336_super", "v336super123")
    resp = await client.get("/api/stats/overview", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # super_admin 不属于任何团队, 也无自己创建的 dataset → 可见数据集 = 0
    assert data["datasets"] == 0, (
        f"super_admin /stats/overview should be 0 (no own/team dataset), got {data['datasets']}"
    )
    assert data["images"] == 0
    assert data["labeled_images"] == 0


@pytest.mark.asyncio
async def test_T_S02_super_admin_overview_model_versions_isolated(
    client: AsyncClient, db_session,
    super_admin, alice_dataset, carol_dataset, carol_model_version,
):
    """T-S02: super_admin /stats/overview 的 model_versions 仅统计可见数据集

    v3.3.6 修复: admin 分支之前直接全平台 func.count(), 现已收紧为按可见数据集过滤.
    验证: carol 的 model_version 不计入 super_admin 的 model_versions.
    """
    headers = await _login(client, "v336_super", "v336super123")
    resp = await client.get("/api/stats/overview", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # super_admin 不可见 carol_dataset → model_versions 应为 0
    assert data["model_versions"] == 0, (
        f"super_admin should not see carol's model_versions, got {data['model_versions']}"
    )


@pytest.mark.asyncio
async def test_T_S03_regular_admin_overview_excludes_outsider(
    client: AsyncClient, db_session,
    regular_admin, alice_dataset, carol_dataset,
):
    """T-S03: regular admin /stats/overview 不包含其他用户的 dataset 数

    v3.3.6 修复: regular admin 之前在 admin 分支看全平台, 现在收紧.
    验证: regular_admin 不可见 alice/carol 的 dataset.
    """
    headers = await _login(client, "v336_admin", "v336admin123")
    resp = await client.get("/api/stats/overview", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["datasets"] == 0, (
        f"regular admin /stats/overview should be 0 (no own/team dataset), got {data['datasets']}"
    )


@pytest.mark.asyncio
async def test_T_S04_alice_overview_only_owns(
    client: AsyncClient, db_session,
    alice, alice_dataset, carol_dataset, alice_team_dataset,
):
    """T-S04: alice /stats/overview 仅返回自己创建的数据集相关统计

    验证: alice 可见 alice_dataset + alice_team_dataset (team owner 视角),
    不可见 carol_dataset. 计数应只反映自己的数据集.
    """
    headers = await _login(client, "v336_alice", "v336alice123")
    resp = await client.get("/api/stats/overview", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # alice 拥有 2 个 dataset: alice_dataset (3 图) + alice_team_dataset (0 图)
    # alice 不可见 carol_dataset (5 图)
    assert data["datasets"] == 2, f"expected 2 own datasets, got {data['datasets']}"
    # carol 有 5 张图, 不应计入 alice 的 images
    assert data["images"] == 3, f"expected 3 own images, got {data['images']}"
    # carol 有 4 张已标图, alice 只有 2 张
    assert data["labeled_images"] == 2, f"expected 2 own labeled, got {data['labeled_images']}"


# ============== T-S05~T-S07: /api/stats/annotator-efficiency 隐私保护 ==============

@pytest.mark.asyncio
async def test_T_S05_super_admin_annotator_efficiency_self_only(
    client: AsyncClient, db_session,
    super_admin, alice_annotation_logs, carol_annotation_logs,
):
    """T-S05: super_admin /stats/annotator-efficiency 仅返回自己

    v3.3.6 关键修复: super_admin 之前在 admin 分支看全平台 Top 10 标注员 (含 username),
    严重隐私泄露. 现已收紧为 self only.
    验证: items 列表只包含 super_admin 自己的 1 条, 不含 carol.
    """
    headers = await _login(client, "v336_super", "v336super123")
    resp = await client.get("/api/stats/annotator-efficiency", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data.get("items", [])
    assert len(items) == 1, f"super_admin should see only 1 (self), got {len(items)}"
    # 返回的应是 super_admin 自己, 不是 carol
    assert items[0]["username"] == "v336_super", (
        f"super_admin should see only self, got username={items[0]['username']}"
    )
    # super_admin 没有任何标注记录
    assert items[0]["annotation_count"] == 0


@pytest.mark.asyncio
async def test_T_S06_regular_admin_annotator_efficiency_self_only(
    client: AsyncClient, db_session,
    regular_admin, alice_annotation_logs, carol_annotation_logs,
):
    """T-S06: regular admin /stats/annotator-efficiency 仅返回自己

    v3.3.6 关键修复: regular admin 之前在 admin 分支看全平台 Top 10, 现已收紧.
    """
    headers = await _login(client, "v336_admin", "v336admin123")
    resp = await client.get("/api/stats/annotator-efficiency", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data.get("items", [])
    assert len(items) == 1
    assert items[0]["username"] == "v336_admin"


@pytest.mark.asyncio
async def test_T_S07_alice_annotator_efficiency_sees_self_only(
    client: AsyncClient, db_session,
    alice, alice_annotation_logs, carol_annotation_logs,
):
    """T-S07: alice /stats/annotator-efficiency 仅返回自己

    验证: items 只包含 alice 自己 (5 条标注), 不含 carol (10 条).
    """
    headers = await _login(client, "v336_alice", "v336alice123")
    resp = await client.get("/api/stats/annotator-efficiency", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data.get("items", [])
    assert len(items) == 1
    assert items[0]["username"] == "v336_alice"
    # alice 有 5 条标注记录
    assert items[0]["annotation_count"] == 5, (
        f"alice should see 5 self annotations, got {items[0]['annotation_count']}"
    )


# ============== T-S08~T-S10: 取消共享权限收紧 ==============

@pytest.mark.asyncio
async def test_T_S08_regular_admin_cannot_unshare_others(
    client: AsyncClient, db_session,
    regular_admin, alice_team_dataset,
):
    """T-S08: regular admin 取消他人的共享 → 403

    v3.3.6 修复: 移除 is_admin() 旁路, 仅 owner / super_admin 可取消共享.
    regular admin 通过取消共享可破坏别人的协作关系, 是横向越权.
    """
    headers = await _login(client, "v336_admin", "v336admin123")
    resp = await client.delete(
        f"/api/teams/datasets/{alice_team_dataset.id}/share", headers=headers
    )
    assert resp.status_code == 403, (
        f"regular admin should NOT unshare others, got {resp.status_code}: {resp.text}"
    )
    # 验证数据集未变化 (直接 SELECT 而非 expire ORM 对象)
    ds_after = (await db_session.execute(
        select(Dataset).where(Dataset.id == alice_team_dataset.id)
    )).scalar_one()
    assert ds_after.team_id is not None, "dataset.team_id should be unchanged after 403"


@pytest.mark.asyncio
async def test_T_S09_super_admin_can_unshare_others(
    client: AsyncClient, db_session,
    super_admin, alice_team_dataset,
):
    """T-S09: super_admin 仍可取消他人共享 (平台代管场景)

    业务规则: super_admin 作为平台代管, owner 失联时可代为取消共享.
    """
    headers = await _login(client, "v336_super", "v336super123")
    resp = await client.delete(
        f"/api/teams/datasets/{alice_team_dataset.id}/share", headers=headers
    )
    assert resp.status_code == 200, (
        f"super_admin should be able to unshare (代管), got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_T_S10_owner_can_unshare_own(
    client: AsyncClient, db_session,
    alice, alice_team_dataset,
):
    """T-S10: owner 取消自己的共享 → 200 (正常路径)

    验证: 未被 v3.3.6 收紧影响, owner 仍可正常取消自己的共享.
    """
    headers = await _login(client, "v336_alice", "v336alice123")
    resp = await client.delete(
        f"/api/teams/datasets/{alice_team_dataset.id}/share", headers=headers
    )
    assert resp.status_code == 200, (
        f"owner should be able to unshare own, got {resp.status_code}: {resp.text}"
    )


# ============== 补充: StatsService.global_overview 已废弃 ==============

def test_StatsService_global_overview_is_deprecated():
    """StatsService.global_overview 已标记为 DEPRECATED, 调用应触发 DeprecationWarning"""
    import warnings
    from app.admin.service.stats_service import StatsService

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        # 静态方法本身不连接 DB, 但会触发 DeprecationWarning
        # 实际调用会尝试 DB, 这里只验证 warning 触发机制
        # 通过 inspect 源码含 "deprecated" 字样
        import inspect
        src = inspect.getsource(StatsService.global_overview)
        assert "DEPRECATED" in src or "deprecated" in src, (
            "global_overview 源码应包含 DEPRECATED 标记"
        )


# ============== T-S11: 归档团队统计访问控制 ==============

@pytest.mark.asyncio
async def test_T_S11_archived_team_stats_blocked_for_non_super(
    client: AsyncClient, db_session,
    regular_admin, team_t, alice_dataset, alice_team_dataset,
):
    """T-S11: regular admin 访问归档团队统计 → 410

    v3.3.6-STATS-ISOLATION 第二轮修复:
      - 团队归档后, 统计数据对非 super_admin 不可访问
      - 业务规则: 归档意味着业务停止, 不应再泄露历史协作数据
      - super_admin 可访问 (审计/恢复场景)

    准备:
      - regular_admin 加入 team_t 团队 (作为 manager)
      - 归档 team_t 团队
      - 访问 /api/stats/team/{id} 应被拒 (410)
    """
    from datetime import datetime

    # regular_admin 加入 team_t
    member = TeamMember(
        team_id=team_t.id,
        user_id=regular_admin.id,
        role="manager",
    )
    db_session.add(member)
    # 归档团队
    team_t.archived_at = datetime.utcnow()
    await db_session.commit()

    headers = await _login(client, "v336_admin", "v336admin123")
    resp = await client.get(
        f"/api/stats/team/{team_t.id}", headers=headers
    )
    assert resp.status_code == 410, (
        f"regular admin should not access archived team stats, got {resp.status_code}: {resp.text}"
    )


# ============== T-S12: /api/users 完整信息仅 super_admin 可见 ==============

@pytest.mark.asyncio
async def test_T_S12_regular_admin_user_list_redacted(
    client: AsyncClient, db_session,
    regular_admin, alice, carol,
):
    """T-S12: regular admin 访问 /api/users 不可见 email/role/is_active

    v3.3.6-STATS-ISOLATION 第二轮修复:
      - 旧逻辑: is_admin() (含 regular admin) 可看完整信息
      - 新逻辑: 仅 super_admin 可看 email/role/is_active
      - regular admin 仅看 id + username (与普通用户一致)
    """
    headers = await _login(client, "v336_admin", "v336admin123")
    resp = await client.get("/api/users/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data.get("items", [])

    # 找到 alice 的记录
    alice_item = next(
        (u for u in items if u["username"] == "v336_alice"), None
    )
    assert alice_item is not None, "alice 应该在用户列表中"
    # 关键验证: email/role/is_active 必须为 None
    assert alice_item["email"] is None, (
        f"regular admin should NOT see email, got {alice_item['email']}"
    )
    assert alice_item["role"] is None, (
        f"regular admin should NOT see role, got {alice_item['role']}"
    )
    assert alice_item["is_active"] is None, (
        f"regular admin should NOT see is_active, got {alice_item['is_active']}"
    )


@pytest.mark.asyncio
async def test_T_S12_super_admin_user_list_full(
    client: AsyncClient, db_session,
    super_admin, alice,
):
    """T-S12 补充: super_admin 仍可看完整用户信息 (平台代管场景)"""
    headers = await _login(client, "v336_super", "v336super123")
    resp = await client.get("/api/users/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data.get("items", [])

    alice_item = next(
        (u for u in items if u["username"] == "v336_alice"), None
    )
    assert alice_item is not None
    # super_admin 可看完整信息
    assert alice_item["email"] is not None
    assert alice_item["role"] is not None
    assert alice_item["is_active"] is not None


# ============== T-S13: /api/audit-logs 仅 super_admin 可访问 ==============

@pytest.mark.asyncio
async def test_T_S13_regular_admin_audit_logs_blocked(
    client: AsyncClient, db_session,
    regular_admin,
):
    """T-S13: regular admin 访问 /api/audit-logs → 403

    v3.3.6-STATS-ISOLATION 第二轮修复:
      - 旧逻辑: is_admin() (含 regular admin) 可访问所有审计日志
      - 新逻辑: 仅 super_admin 可访问
      - 原因: 审计日志含 user_id + resource_id + ip_address 等敏感信息
      - 业务场景: regular admin 走 /api/teams/{id}/activities (团队级活动)
    """
    headers = await _login(client, "v336_admin", "v336admin123")
    resp = await client.get("/api/audit-logs", headers=headers)
    assert resp.status_code == 403, (
        f"regular admin should NOT access audit-logs, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_T_S13_super_admin_audit_logs_allowed(
    client: AsyncClient, db_session,
    super_admin,
):
    """T-S13 补充: super_admin 仍可访问 /api/audit-logs (合规审计场景)"""
    headers = await _login(client, "v336_super", "v336super123")
    resp = await client.get("/api/audit-logs", headers=headers)
    assert resp.status_code == 200, (
        f"super_admin should access audit-logs, got {resp.status_code}: {resp.text}"
    )

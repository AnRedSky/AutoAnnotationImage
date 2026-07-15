<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Grid, CircleCheck, Aim, TrendCharts, Delete, Search, Refresh, Filter,
  VideoPlay, VideoPause
} from '@element-plus/icons-vue'
import { modelApi } from '@/api'

const data = ref<any[]>([])
const loading = ref(false)
const selectedRows = ref<any[]>([])
const selectedIds = computed(() => selectedRows.value.map((r) => r.id))
const detail = ref<any>(null)
const compare = ref<any>(null)
const detailOpen = ref(false)
const compareOpen = ref(false)
const batchDeleting = ref(false)

// ============== 筛选条件 (即时生效, 无需提交) ==============
// 关键词: 模糊匹配 model.name 和 model.base_model
const filterKeyword = ref('')
// 数据集下拉筛选: '' 表示全部
const filterDatasetId = ref<number | ''>('')

// 分页 (client-side, 后端 list 当前不分页)
const page = ref(1)
const pageSize = ref(10)
// total 始终指向 filteredData 长度, 让分页器在筛选后正确显示「X 条」
const total = computed(() => filteredData.value.length)
// 表格绑定的数据先经过 filter -> 再做分页
const pagedData = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredData.value.slice(start, start + pageSize.value)
})
// 筛选条件变更时自动回到第 1 页 (避免筛选后停在空页)
const onFilterChange = () => { page.value = 1 }
const onPageChange = (p: number) => { page.value = p }
const onSizeChange = (s: number) => { pageSize.value = s; page.value = 1 }
/** 表格序号: 当前页 = (page - 1) * pageSize + 行索引 (从 1 开始) */
const indexMethod = (idx: number) => (page.value - 1) * pageSize.value + idx + 1

// ============== 数据集下拉选项 (用于筛选) ==============
// 从 model list 推导出「出现过的 dataset_id」, 同时按数据集 id 升序去重
// 比直接调 /api/datasets 列表更省一次往返, 也避免无关数据集污染下拉
// 选项 label 只用 dataset_name, 不再加 ID 前缀 (按产品要求)
const datasetOptions = computed(() => {
  const seen = new Map<number, { id: number; name: string }>()
  for (const m of data.value) {
    if (m.dataset_id != null && !seen.has(m.dataset_id)) {
      // 优先用后端 join 出的 dataset_name, 缺失时回退到「未命名数据集」(不带 ID)
      seen.set(m.dataset_id, {
        id: m.dataset_id,
        name: m.dataset_name || '未命名数据集',
      })
    }
  }
  return Array.from(seen.values()).sort((a, b) => a.id - b.id)
})

// ============== 筛选后数据 (即时, 客户端计算) ==============
const filteredData = computed(() => {
  const kw = filterKeyword.value.trim().toLowerCase()
  return data.value.filter((m: any) => {
    // 关键词: 模型名 + 基础模型 双字段模糊匹配
    if (kw) {
      const hay = `${m.name || ''} ${m.base_model || ''}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    // 数据集下拉
    if (filterDatasetId.value !== '' && m.dataset_id !== filterDatasetId.value) {
      return false
    }
    return true
  })
})

// ============== 顶部统计 (基于全量 data 聚合) ==============
const stats = computed(() => {
  const total = data.value.length
  const active = data.value.filter((m: any) => m.is_active).length
  // 取所有模型中 accuracy 最高的作为最佳
  let bestAcc = 0
  let bestName = '-'
  for (const m of data.value) {
    const a = Number(m.accuracy || 0)
    if (a > bestAcc) {
      bestAcc = a
      bestName = m.name
    }
  }
  // 基础模型去重
  const bases = new Set(data.value.map((m: any) => m.base_model).filter(Boolean))
  return {
    total,
    active,
    bestAcc,
    bestName,
    baseCount: bases.size,
  }
})

const load = async () => {
  loading.value = true
  try {
    const res: any = await modelApi.list()
    data.value = res?.items || res || []
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
  } finally { loading.value = false }
}

onMounted(load)

const onActivate = async (id: number) => {
  try {
    await modelApi.activate(id)
    ElMessage.success('已激活该版本 (允许多激活并存)')
    load()
  } catch (e: any) {
    ElMessage.error('激活失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onDeactivate = async (id: number) => {
  try {
    await ElMessageBox.confirm('确认取消该模型的激活状态?', '取消激活', { type: 'warning' })
  } catch { return }
  try {
    await modelApi.deactivate(id)
    ElMessage.success('已取消激活')
    load()
  } catch (e: any) {
    ElMessage.error('取消激活失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// ============== 批量激活 / 批量取消激活 ==============
const batchActivating = ref(false)
const onBatchSetActive = async (active: boolean) => {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先选择模型版本')
    return
  }
  const action = active ? '激活' : '取消激活'
  // 拆出已在目标态 / 需变更 两类, 给用户更精确的反馈
  const need: any[] = []
  const skip: any[] = []
  for (const r of selectedRows.value) {
    if (r.is_active === active) skip.push(r)
    else need.push(r)
  }
  if (need.length === 0) {
    ElMessage.info(`所选 ${skip.length} 个版本已全部为${active ? '激活' : '未激活'}, 无需操作`)
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认${action}选中的 ${need.length} 个版本?` +
        (skip.length ? `另有 ${skip.length} 个版本已为${active ? '激活' : '未激活'}, 会被跳过` : ''),
      `批量${action}`,
      { type: active ? 'success' : 'warning' }
    )
  } catch { return }
  batchActivating.value = true
  try {
    const r: any = await modelApi.batchSetActive(need.map((x) => x.id), active)
    if (r.success) {
      ElMessage.success(`已${action} ${r.ids.length} 个版本`)
      selectedRows.value = []
    } else {
      ElMessage.warning(r.message || `${action}失败`)
    }
    await load()
  } catch (e: any) {
    ElMessage.error(`批量${action}失败: ` + (e?.response?.data?.detail || e?.message))
  } finally {
    batchActivating.value = false
  }
}

const onDetail = async (id: number) => {
  try {
    const d: any = await modelApi.detail(id)
    detail.value = d
    detailOpen.value = true
  } catch {
    ElMessage.error('详情加载失败')
  }
}

const onCompare = async () => {
  if (selectedIds.value.length !== 2) {
    ElMessage.warning('请选择恰好 2 个版本进行对比')
    return
  }
  try {
    const c: any = await modelApi.compare(selectedIds.value[0], selectedIds.value[1])
    compare.value = c
    compareOpen.value = true
  } catch (e: any) {
    ElMessage.error('对比失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onDelete = async (row: any) => {
  // v2 改造: 允许删除激活模型 (删除即取消激活)
  const tip = row.is_active
    ? `确定要删除当前已激活的模型版本「${row.name}」(ID=${row.id}) 吗？\n删除即取消激活, 此操作不可恢复, 训练历史会被保留但与该版本解绑。`
    : `确定要删除模型版本「${row.name}」(ID=${row.id}) 吗？此操作不可恢复，相关的训练历史记录会被保留但会与该版本解绑。`
  try {
    await ElMessageBox.confirm(tip, '删除确认', {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return  // 用户取消
  }
  try {
    const res: any = await modelApi.remove(row.id)
    const extra = res.was_active ? ' (已同步取消激活)' : ''
    const fileMsg = res?.deleted_file ? '（已同时删除权重文件）' : ''
    ElMessage.success(`已删除模型版本「${row.name}」${extra}${fileMsg}`)
    // 清理已选项中已删除的 id
    selectedRows.value = selectedRows.value.filter((r) => r.id !== row.id)
    load()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

/**
 * 批量删除已选中的模型版本.
 * - v2 改造: 允许包含激活模型 (后端 200, 同步取消激活)
 * - 单事务, 任一失败全部回滚 (id 不存在会 404)
 */
const onBatchDelete = async () => {
  if (selectedRows.value.length === 0) return

  // v2: 不再拆分 activePicked, 全部参与删除
  const deletable = selectedRows.value
  const activePicked = deletable.filter((r) => r.is_active)
  const activeHint = activePicked.length > 0
    ? `\n其中 ${activePicked.length} 个为已激活版本, 删除将同步取消激活。`
    : ''

  // 名称预览最多展示 3 个, 超出用 +N 形式
  const preview = deletable
    .slice(0, 3)
    .map((r) => r.name)
    .join('、')
  const more = deletable.length > 3 ? ` 等 ${deletable.length} 个` : ''

  try {
    await ElMessageBox.confirm(
      `确定要批量删除「${preview}${more}」吗？此操作不可恢复, 相关的训练历史记录会保留但会与这些版本解绑。${activeHint}`,
      '批量删除确认',
      {
        confirmButtonText: `确定删除 ${deletable.length} 个`,
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
  } catch {
    return
  }

  batchDeleting.value = true
  try {
    const res: any = await modelApi.batchRemove(deletable.map((r) => r.id))
    const fd = res?.files_deleted || 0
    const fileMsg = fd > 0 ? `（已同时删除 ${fd} 个权重文件）` : ''
    ElMessage.success(
      `已批量删除 ${res.deleted_ids.length} 个模型版本${fileMsg}`,
    )
    selectedRows.value = []
    load()
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message
    ElMessage.error('批量删除失败: ' + detail)
  } finally {
    batchDeleting.value = false
  }
}

const onSelectionChange = (rows: any[]) => {
  selectedRows.value = rows
}

const pct = (v: any) => (v != null ? `${(Number(v) * 100).toFixed(2)}%` : '-')
const f1fmt = (v: any) => (v != null ? Number(v).toFixed(3) : '-')

// ============== 筛选重置 ==============
const resetFilters = () => {
  filterKeyword.value = ''
  filterDatasetId.value = ''
  page.value = 1
}
</script>

<template>
  <div class="page-flex">
    <!-- ============== 顶部统计条 ============== -->
    <el-row :gutter="14" class="stats-row">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--blue">
          <div class="stat-icon"><el-icon><Grid /></el-icon></div>
          <el-statistic title="模型版本数" :value="stats.total" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--green">
          <div class="stat-icon"><el-icon><Aim /></el-icon></div>
          <el-statistic title="当前激活" :value="stats.active" suffix="个" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--orange">
          <div class="stat-icon"><el-icon><TrendCharts /></el-icon></div>
          <el-statistic
            title="最高准确率"
            :value="stats.bestAcc * 100"
            :precision="2"
            suffix="%"
            :value-style="{ color: '#ff8a4c' }"
          />
          <div class="stat-meta">{{ stats.bestName }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card stat-card--purple">
          <div class="stat-icon"><el-icon><CircleCheck /></el-icon></div>
          <el-statistic title="基础模型数" :value="stats.baseCount" suffix="种" />
        </el-card>
      </el-col>
    </el-row>

    <!-- ============== 顶部操作栏 ============== -->
    <div class="page-header">
      <h2 class="page-title">
        <span>模型版本管理</span>
        <span class="subtitle">Models</span>
      </h2>
      <div class="header-actions">
        <span class="selection-tip">
          已选 <strong>{{ selectedIds.length }}</strong> 个版本
        </span>
        <!-- 批量激活 -->
        <el-tooltip content="将选中的版本全部设为激活状态" placement="top">
          <el-button
            type="success"
            plain
            :icon="VideoPlay"
            :disabled="selectedIds.length === 0 || batchActivating"
            :loading="batchActivating"
            @click="onBatchSetActive(true)"
          >
            批量激活<span v-if="selectedIds.length > 0"> ({{ selectedIds.length }})</span>
          </el-button>
        </el-tooltip>
        <!-- 批量取消激活 -->
        <el-tooltip content="将选中的版本全部设为未激活状态" placement="top">
          <el-button
            type="info"
            plain
            :icon="VideoPause"
            :disabled="selectedIds.length === 0 || batchActivating"
            :loading="batchActivating"
            @click="onBatchSetActive(false)"
          >
            批量取消激活<span v-if="selectedIds.length > 0"> ({{ selectedIds.length }})</span>
          </el-button>
        </el-tooltip>
        <el-button
          type="danger"
          plain
          :icon="Delete"
          :disabled="selectedIds.length === 0 || batchDeleting"
          :loading="batchDeleting"
          @click="onBatchDelete"
        >
          批量删除<span v-if="selectedIds.length > 0"> ({{ selectedIds.length }})</span>
        </el-button>
        <el-button
          type="primary"
          :disabled="selectedIds.length !== 2"
          @click="onCompare"
        >
          对比所选
        </el-button>
      </div>
    </div>

    <!-- ============== 筛选条件栏 (即时生效, 无需提交) ============== -->
    <div class="filter-row">
      <el-input
        v-model="filterKeyword"
        :prefix-icon="Search"
        clearable
        placeholder="搜索模型名 / 基础模型"
        class="filter-keyword"
        @input="onFilterChange"
      />
      <el-select
        v-model="filterDatasetId"
        clearable
        placeholder="按数据集筛选"
        class="filter-dataset"
        @change="onFilterChange"
      >
        <el-option label="全部数据集" value="" />
        <el-option
          v-for="ds in datasetOptions" :key="ds.id"
          :label="ds.name"
          :value="ds.id"
        />
      </el-select>
      <div class="filter-tip">
        <el-icon><Filter /></el-icon>
        <span>共 <strong>{{ filteredData.length }}</strong> / {{ data.length }} 条</span>
        <el-button
          v-if="filterKeyword || filterDatasetId !== ''"
          link
          type="primary"
          size="small"
          @click="resetFilters"
        >清空筛选</el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="pagedData" border stripe class="data-table"
      @selection-change="onSelectionChange">
      <el-table-column type="index" :index="indexMethod" label="#" width="42" />
      <el-table-column type="selection" width="40" />
      <el-table-column prop="name" label="模型名" min-width="200">
        <template #default="{ row }">
          <div class="model-name-cell">
            <div class="model-icon">
              <el-icon><Grid /></el-icon>
            </div>
            <span :class="{ 'is-active-name': row.is_active }">{{ row.name }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="base_model" label="基础模型" min-width="200">
        <template #default="{ row }">
          <el-tag size="small" type="info" effect="plain">{{ row.base_model }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="is_active" label="状态" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.is_active" type="success" effect="dark" size="small">
            <el-icon style="margin-right: 2px;"><CircleCheck /></el-icon>已激活
          </el-tag>
          <el-tag v-else effect="plain" size="small">未激活</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="accuracy" label="准确率" width="100">
        <template #default="{ row }">
          <span :class="['metric', 'metric--acc', { 'is-strong': Number(row.accuracy || 0) >= 0.8 }]">
            {{ pct(row.accuracy) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="precision" label="精确率" width="100">
        <template #default="{ row }">{{ pct(row.precision) }}</template>
      </el-table-column>
      <el-table-column prop="recall" label="召回率" width="100">
        <template #default="{ row }">{{ pct(row.recall) }}</template>
      </el-table-column>
      <el-table-column prop="f1_score" label="F1" width="80">
        <template #default="{ row }">{{ f1fmt(row.f1_score) }}</template>
      </el-table-column>
      <el-table-column prop="dataset_id" label="训练集" min-width="140">
        <template #default="{ row }">
          <el-tooltip v-if="row.dataset_name" :content="`数据集 ID: ${row.dataset_id}`" placement="top">
            <span class="ds-name">
              <el-icon><Grid /></el-icon>
              {{ row.dataset_name }}
            </span>
          </el-tooltip>
          <span v-else class="ds-id">{{ row.dataset_id ?? '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="num_classes" label="类别数" width="80">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" type="warning">{{ row.num_classes }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" min-width="170">
        <template #default="{ row }">{{ row.created_at ? new Date(row.created_at).toLocaleString() : '-' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <!--
            激活 / 取消激活 互斥按钮
            - 未激活: 显示「激活」(success 绿, 提示)
            - 已激活: 显示「取消激活」(info 灰, 提示)
            - 互斥: 同时存在, 状态切换互不影响
            - v2 改造: 允许多激活, 不再自动取消同 dataset 其他
          -->
          <template v-if="!row.is_active">
            <el-tooltip content="激活该模型版本 (允许多激活并存)" placement="top">
              <el-button
                size="small"
                type="success"
                @click="onActivate(row.id)"
              >激活</el-button>
            </el-tooltip>
          </template>
          <template v-else>
            <el-tooltip content="取消该模型版本的激活状态" placement="top">
              <el-button
                size="small"
                type="info"
                plain
                @click="onDeactivate(row.id)"
              >取消激活</el-button>
            </el-tooltip>
          </template>
          <el-button size="small" @click="onDetail(row.id)">详情</el-button>
          <!--
            删除按钮: v2 改造, 允许删除激活模型 (删除即取消激活)
          -->
          <el-tooltip content="删除此模型版本 (激活态会同步取消激活)" placement="top">
            <el-button
              size="small"
              type="danger"
              @click="onDelete(row)"
            >删除</el-button>
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <!-- 分页栏: 固定在页面底部 (position: sticky 兜底) -->
    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </div>

    <!-- 详情弹窗 -->
    <el-dialog v-model="detailOpen" :title="`模型详情 - ${detail?.name || ''}`" width="720px">
      <div v-if="detail">
        <!-- 顶部信息卡 (含激活状态徽章) -->
        <div class="detail-hero" :class="{ 'is-active': detail.is_active }">
          <div class="hero-left">
            <div class="hero-mark">
              <el-icon><Grid /></el-icon>
            </div>
            <div>
              <div class="hero-name">{{ detail.name }}</div>
              <div class="hero-base">{{ detail.base_model }} · 类别数 {{ detail.num_classes }}</div>
            </div>
          </div>
          <el-tag v-if="detail.is_active" type="success" effect="dark">当前激活</el-tag>
          <el-tag v-else effect="plain">未激活</el-tag>
        </div>

        <!-- 关键指标 4 卡 -->
        <el-row :gutter="12" class="metrics-row">
          <el-col :span="6">
            <div class="metric-tile metric-tile--blue">
              <div class="metric-label">准确率</div>
              <div class="metric-value">{{ pct(detail.accuracy) }}</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="metric-tile metric-tile--green">
              <div class="metric-label">精确率</div>
              <div class="metric-value">{{ pct(detail.precision) }}</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="metric-tile metric-tile--orange">
              <div class="metric-label">召回率</div>
              <div class="metric-value">{{ pct(detail.recall) }}</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="metric-tile metric-tile--purple">
              <div class="metric-label">F1</div>
              <div class="metric-value">{{ f1fmt(detail.f1_score) }}</div>
            </div>
          </el-col>
        </el-row>

        <el-descriptions :column="2" border size="small" style="margin-top: 12px;">
          <el-descriptions-item label="模型 ID">{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="数据集 ID">{{ detail.dataset_id }}</el-descriptions-item>
          <el-descriptions-item label="基础模型">{{ detail.base_model }}</el-descriptions-item>
          <el-descriptions-item label="类别数">{{ detail.num_classes }}</el-descriptions-item>
          <el-descriptions-item label="模型文件" :span="2">
            <code class="path-code">{{ detail.file_path }}</code>
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </el-dialog>

    <!-- 对比弹窗 -->
    <el-dialog v-model="compareOpen" title="模型版本对比" width="820px">
      <div v-if="compare">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-card shadow="never" class="compare-card compare-card--a">
              <div class="compare-head">
                <el-icon><Grid /></el-icon>
                <span>{{ compare.model_a.name }}</span>
              </div>
              <div class="compare-base">基础模型: {{ compare.model_a.base_model }}</div>
              <el-statistic
                title="准确率" :value="Number(compare.model_a.accuracy ?? 0)" :precision="4"
                :value-style="{ color: '#4f7cff', fontWeight: 600 }"
              />
            </el-card>
          </el-col>
          <el-col :span="12">
            <el-card shadow="never" class="compare-card compare-card--b">
              <div class="compare-head">
                <el-icon><Grid /></el-icon>
                <span>{{ compare.model_b.name }}</span>
              </div>
              <div class="compare-base">基础模型: {{ compare.model_b.base_model }}</div>
              <el-statistic
                title="准确率" :value="Number(compare.model_b.accuracy ?? 0)" :precision="4"
                :value-style="{ color: '#00c48c', fontWeight: 600 }"
              />
            </el-card>
          </el-col>
        </el-row>

        <el-card header="指标差异 (A - B)" style="margin-top: 16px;" shadow="never" class="delta-card">
          <el-row :gutter="16">
            <el-col :span="6">
              <el-statistic
                title="准确率 Δ"
                :value="Number(compare.delta.accuracy ?? 0)"
                :precision="4"
                :value-style="{ color: (compare.delta.accuracy ?? 0) >= 0 ? '#00c48c' : '#ff4d4f', fontWeight: 600 }"
              />
            </el-col>
            <el-col :span="6">
              <el-statistic
                title="精确率 Δ"
                :value="Number(compare.delta.precision ?? 0)"
                :precision="4"
                :value-style="{ color: (compare.delta.precision ?? 0) >= 0 ? '#00c48c' : '#ff4d4f' }"
              />
            </el-col>
            <el-col :span="6">
              <el-statistic
                title="召回率 Δ"
                :value="Number(compare.delta.recall ?? 0)"
                :precision="4"
                :value-style="{ color: (compare.delta.recall ?? 0) >= 0 ? '#00c48c' : '#ff4d4f' }"
              />
            </el-col>
            <el-col :span="6">
              <el-statistic
                title="F1 Δ"
                :value="Number(compare.delta.f1_score ?? 0)"
                :precision="4"
                :value-style="{ color: (compare.delta.f1_score ?? 0) >= 0 ? '#00c48c' : '#ff4d4f' }"
              />
            </el-col>
          </el-row>
        </el-card>

        <el-card
          v-if="compare.model_a.confusion_matrix"
          header="混淆矩阵（模型 A）"
          style="margin-top: 16px;"
          shadow="never"
        >
          <pre class="cm-pre">{{ JSON.stringify(compare.model_a.confusion_matrix, null, 2) }}</pre>
        </el-card>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 页面: 撑满 el-main, flex column 布局, 表格 flex:1, 分页栏钉在底部 */
.page-flex {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 16px;
}

/* ============== 顶部统计条 ============== */
.stats-row { margin-bottom: 16px; flex-shrink: 0; }
.stat-card {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-lg) !important;
  background: #fff !important;
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
}
.stat-card--blue::before { background: var(--gradient-brand); }
.stat-card--green::before { background: var(--gradient-success); }
.stat-card--orange::before { background: var(--gradient-warm); }
.stat-card--purple::before { background: linear-gradient(135deg, #722ed1 0%, #531dab 100%); }

.stat-card :deep(.el-card__body) {
  padding: 20px 22px;
  position: relative;
}
.stat-card :deep(.el-statistic__head) {
  color: var(--text-secondary) !important;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 6px;
}
.stat-card :deep(.el-statistic__content) {
  font-size: 26px;
  font-weight: 600;
  color: var(--text-primary);
}

.stat-icon {
  position: absolute;
  right: 18px;
  top: 18px;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stat-icon :deep(.el-icon) { font-size: 20px; }
.stat-card--blue .stat-icon { background: rgba(79, 124, 255, 0.1); color: #4f7cff; }
.stat-card--green .stat-icon { background: rgba(0, 196, 140, 0.1); color: #00c48c; }
.stat-card--orange .stat-icon { background: rgba(255, 138, 76, 0.1); color: #ff8a4c; }
.stat-card--purple .stat-icon { background: rgba(114, 46, 209, 0.1); color: #722ed1; }

.stat-meta {
  color: var(--text-placeholder);
  margin-top: 6px;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ============== 顶部操作栏 ============== */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-shrink: 0;
}
.page-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}
.page-title .subtitle {
  color: var(--text-placeholder);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.selection-tip {
  color: var(--text-secondary);
  font-size: 13px;
}
.selection-tip strong {
  color: var(--brand-primary);
  font-weight: 600;
  font-size: 14px;
}

/* ============== 表格 ============== */
.data-table {
  flex: 1 1 0;
  min-height: 0;
  height: 100% !important;
  border-radius: var(--radius-md) !important;
  overflow: hidden;
}

.model-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.model-icon {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  background: var(--gradient-brand);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.model-icon :deep(.el-icon) { font-size: 14px; }
.is-active-name { font-weight: 600; color: var(--text-primary); }

.metric { font-variant-numeric: tabular-nums; font-weight: 500; }
.metric--acc.is-strong { color: #00c48c; font-weight: 600; }

.ds-name {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-primary);
}
.ds-name :deep(.el-icon) { color: #4f7cff; font-size: 13px; }
.ds-id { color: var(--text-placeholder); font-family: var(--font-mono); font-size: 12px; }

/* ============== 筛选条件栏 ============== */
.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: #fff;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-soft);
  flex-shrink: 0;
  flex-wrap: nowrap;
}
.filter-keyword { width: 240px; }
.filter-dataset { width: 200px; }
.filter-scene   { width: 170px; }
.filter-tip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  margin-left: auto;
}
.filter-tip :deep(.el-icon) { color: var(--brand-primary); }
.filter-tip strong { color: var(--brand-primary); font-weight: 600; }

/* ============== 分页栏 ============== */
.pager {
  flex-shrink: 0;
  margin-top: 12px;
  padding: 8px 0;
  display: flex;
  justify-content: flex-end;
  background: #fff;
  border-top: 1px solid var(--border-soft);
  position: sticky;
  bottom: 0;
  z-index: 5;
}

/* ============== 详情弹窗 ============== */
.detail-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, rgba(79, 124, 255, 0.06) 0%, rgba(110, 81, 233, 0.06) 100%);
  border: 1px solid rgba(79, 124, 255, 0.12);
  margin-bottom: 16px;
}
.detail-hero.is-active {
  background: linear-gradient(135deg, rgba(0, 196, 140, 0.08) 0%, rgba(0, 163, 224, 0.08) 100%);
  border-color: rgba(0, 196, 140, 0.18);
}
.hero-left { display: flex; align-items: center; gap: 12px; }
.hero-mark {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: var(--gradient-brand);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  box-shadow: 0 4px 12px rgba(79, 124, 255, 0.3);
}
.detail-hero.is-active .hero-mark {
  background: var(--gradient-success);
  box-shadow: 0 4px 12px rgba(0, 196, 140, 0.3);
}
.hero-name { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.hero-base { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }

.metrics-row { margin-top: 4px; }
.metric-tile {
  border-radius: var(--radius-md);
  padding: 14px 16px;
  background: #fff;
  border: 1px solid var(--border-soft);
  text-align: center;
}
.metric-tile--blue   { background: linear-gradient(135deg, #f0f4ff 0%, #e9ecff 100%); border-color: rgba(79, 124, 255, 0.18); }
.metric-tile--green  { background: linear-gradient(135deg, #e6fbf3 0%, #d9f5ec 100%); border-color: rgba(0, 196, 140, 0.18); }
.metric-tile--orange { background: linear-gradient(135deg, #fff2e9 0%, #ffe7d6 100%); border-color: rgba(255, 138, 76, 0.18); }
.metric-tile--purple { background: linear-gradient(135deg, #f4e9ff 0%, #ead7ff 100%); border-color: rgba(114, 46, 209, 0.18); }
.metric-label { font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }
.metric-value { font-size: 18px; font-weight: 600; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.metric-tile--blue   .metric-value { color: #4f7cff; }
.metric-tile--green  .metric-value { color: #00c48c; }
.metric-tile--orange .metric-value { color: #ff8a4c; }
.metric-tile--purple .metric-value { color: #722ed1; }

.path-code {
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--bg-soft);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--text-regular);
  word-break: break-all;
}

/* ============== 对比弹窗 ============== */
.compare-card { border-radius: var(--radius-md) !important; }
.compare-card.compare-card--a { border-top: 3px solid #4f7cff !important; }
.compare-card.compare-card--b { border-top: 3px solid #00c48c !important; }
.compare-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}
.compare-head :deep(.el-icon) { font-size: 16px; }
.compare-card--a .compare-head :deep(.el-icon) { color: #4f7cff; }
.compare-card--b .compare-head :deep(.el-icon) { color: #00c48c; }
.compare-base {
  margin-bottom: 12px;
  color: var(--text-secondary);
  font-size: 12px;
}
.delta-card { border-left: 3px solid #ffa940 !important; }

.cm-pre {
  font-size: 11px;
  overflow: auto;
  max-height: 240px;
  background: var(--bg-soft);
  padding: 12px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  margin: 0;
}
</style>

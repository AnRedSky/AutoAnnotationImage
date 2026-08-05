<script setup lang="ts">
/**
 * Datasets.vue - 数据集列表 + 上传队列 + 类别管理 + AI 预标注 + 导出
 */
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, View, Delete, Download, CollectionTag, Lightning, Folder, Share, UserFilled
} from '@element-plus/icons-vue'
import { datasetApi, autoAnnotateApi, exportApi } from '@/api'
// v2.5.8 架构优化: 业务组件全部迁入当前页面私有目录
import UploadQueue from './components/UploadQueue.vue'
import {
  getTaskTypeMeta, TASK_TYPE_OPTIONS
} from '@/utils/taskType'

const router = useRouter()

// 全量数据 (后端不分页, 前端做 client-side 分页 + 序号列)
const data = ref<any[]>([])
const loading = ref(false)
const uploadDs = ref<any>(null)
// 修复: v-model 必须是 Boolean, 拆出 uploadOpen 与 uploadDs 各司其职
const uploadOpen = ref(false)
const catDs = ref<any>(null)
// 修复: v-model 必须是 Boolean, 不能复用 dataset 对象作为 v-model
// 拆成 catOpen (boolean) + catDs (dataset | null)
const catOpen = ref(false)
const categories = ref<any[]>([])
const newCatName = ref('')

// v2.2.0 S9.2: 检测统计弹窗
const statsOpen = ref(false)
const statsDs = ref<any>(null)
const statsData = ref<any>(null)
const statsLoading = ref(false)

// 分页 (client-side)
const page = ref(1)
const pageSize = ref(10)
const total = computed(() => data.value.length)
const pagedData = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return data.value.slice(start, start + pageSize.value)
})
const onPageChange = (p: number) => { page.value = p }
const onSizeChange = (s: number) => { pageSize.value = s; page.value = 1 }
/** 表格序号: 当前页 = (page - 1) * pageSize + 行索引 (从 1 开始) */
const indexMethod = (idx: number) => (page.value - 1) * pageSize.value + idx + 1

const createOpen = ref(false)
// 预置类别: 数组形式管理, 支持单条删除/批量增加 (替代 textarea 逗号分隔)
// 关闭对话框或成功后重置为 [] (与空状态对齐)
const createForm = ref<{
  name: string
  description: string
  task_type: string
  category_names: string[]
}>({
  name: '',
  description: '',
  task_type: 'classification',
  category_names: []
})
// 增加一条空类别输入项 (enqueue)
const addCategoryRow = () => {
  createForm.value.category_names.push('')
}
// 删除一条类别 (含确认弹窗, 防止误操作; 空内容不弹确认, 直接删)
const removeCategoryRow = async (idx: number) => {
  const item = createForm.value.category_names[idx]
  if (item && item.trim()) {
    try {
      await ElMessageBox.confirm(
        `确认删除类别"${item.trim()}"？此操作仅移除该项输入，不会影响已保存的类别。`,
        '删除确认',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
      )
    } catch {
      return
    }
  }
  createForm.value.category_names.splice(idx, 1)
}

const load = async () => {
  loading.value = true
  try {
    const res: any = await datasetApi.list()
    // v3.3.2: 响应可能是 { items, total, personal_count, team_shared_count } 或旧数组
    if (res && Array.isArray(res.items)) {
      data.value = res.items
      personalTotal.value = res.personal_count || res.items.length
      teamSharedTotal.value = res.team_shared_count || 0
    } else {
      data.value = res || []
      personalTotal.value = data.value.length
      teamSharedTotal.value = 0
    }
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

/**
 * v3.3.2 数据来源统计 (用于页面标题 + 来源列)
 * - personalTotal: 个人所有 dataset 数量
 * - teamSharedTotal: 团队共享 dataset 数量
 */
const personalTotal = ref(0)
const teamSharedTotal = ref(0)

const loadCategories = async (dsId: number) => {
  try {
    const c: any = await datasetApi.categories(dsId)
    categories.value = c?.items || c || []
  } catch (e) {}
}

onMounted(() => load())

watch(catDs, (v) => { if (v) loadCategories(v.id) })

/**
 * 类别汇总: 弹窗顶部 4 张统计卡的实时数据
 * (基于后端 list_categories 实时聚合返回, 这里只做前端求和, 不再二次拉接口)
 */
const totalHuman = computed(
  () => categories.value.reduce((sum, c) => sum + (c.human_labeled_count || 0), 0)
)
const totalAi = computed(
  () => categories.value.reduce((sum, c) => sum + (c.ai_labeled_count || 0), 0)
)
const totalCandidate = computed(
  () => categories.value.reduce((sum, c) => sum + (c.ai_candidate_count || 0), 0)
)
// 全数据集已落标的总样本 (已确认 + AI 已标), 用于计算每个类别的占比分母
const grandTotal = computed(
  () => totalHuman.value + totalAi.value
)
// 同步给 categories 每行附加 totalPct 字段 (用 watch + 浅赋值避免改原引用)
watch(
  [categories, grandTotal],
  () => {
    const denom = grandTotal.value || 1
    categories.value.forEach((c: any) => {
      c.totalPct = Math.round(((c.sample_count || 0) / denom) * 100)
    })
  },
  { immediate: true, deep: true }
)

const onCreate = async () => {
  const v = createForm.value
  if (!v.name) { ElMessage.warning('请输入名称'); return }
  // 数组形式: 每项 trim, 过滤空值, 去重保序 (后端期望 string[])
  const names = Array.from(
    new Set(
      (v.category_names || [])
        .map((s: string) => s.trim())
        .filter(Boolean)
    )
  )
  try {
    await datasetApi.create({ ...v, category_names: names })
    ElMessage.success('创建成功')
    createOpen.value = false
    createForm.value = {
      name: '',
      description: '',
      task_type: 'classification',
      category_names: []
    }
    load()
  } catch (e: any) {
    ElMessage.error('创建失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onDelete = async (id: number) => {
  try {
    await ElMessageBox.confirm('确认删除该数据集？所有图片/类别/标注都会一起删除', '警告', { type: 'warning' })
  } catch { return }
  try {
    await datasetApi.remove(id)
    ElMessage.success('已删除')
    load()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onAutoAnnotate = async (ds: any) => {
  try {
    const res: any = await autoAnnotateApi.run({
      dataset_id: ds.id,
      model_name: 'efficientnet_b0',
      confidence_threshold: 0.6,
      async_mode: false
    })
    ElMessage.success(`AI 预标注完成: 共 ${res.total} 张, 命中 ${res.auto_labeled} 张, 需人工 ${res.need_human} 张`)
  } catch (e: any) {
    ElMessage.error('AI 预标注失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const onUpload = (_result: any) => {
  // UploadQueue 已 emit uploaded 事件, 这里可以刷新计数
  // 由于 UploadQueue 在弹窗内, 用户关闭时再 load
}

const onAddCategory = async () => {
  if (!newCatName.value.trim() || !catDs.value) return
  try {
    await datasetApi.createCategory(catDs.value.id, { name: newCatName.value.trim() })
    newCatName.value = ''
    loadCategories(catDs.value.id)
    ElMessage.success('已添加类别')
    load()  // 刷新 category_count
  } catch (e: any) {
    ElMessage.error('添加失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const handleExport = async (ds: any, format: 'coco' | 'yolo' | 'csv') => {
  const url = exportApi[format](ds.id)
  const token = localStorage.getItem('token')
  try {
    const r = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
    const blob = await r.blob()
    const ext = format === 'yolo' ? 'zip' : format === 'coco' ? 'json' : 'csv'
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${ds.name || 'dataset'}_${ds.id}.${ext}`
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e: any) {
    ElMessage.error('导出失败: ' + (e?.message))
  }
}

const openDetail = (ds: any) => {
  router.push(`/datasets/${ds.id}`)
}

const openUpload = (ds: any) => {
  uploadDs.value = ds
  uploadOpen.value = true
}

const openCategory = (ds: any) => {
  catDs.value = ds
  catOpen.value = true
}

/**
 * v3.3.2: 数据来源列渲染
 * - personal: 蓝色「个人所有」标签
 * - team_shared: 绿色「团队共享」标签 + tooltip 显示团队名
 *
 * 返回: { type, label, tooltip }
 */
const getSourceMeta = (row: any) => {
  if (row?.source === 'team_shared') {
    return {
      type: 'success',
      label: '团队共享',
      tooltip: row.team_name
        ? `共享自团队: ${row.team_name}${row.shared_by ? ` · 共享者: ${row.shared_by}` : ''}`
        : '团队共享',
    }
  }
  // 默认: 个人所有
  return {
    type: 'primary',
    label: '个人所有',
    tooltip: '我创建的数据集',
  }
}

/** v3.3.2: 我的访问权限中文标签 */
const getMyAccessLabel = (access?: string) => {
  const m: Record<string, string> = {
    owner: '所有者',
    admin: '管理员',
    manager: '可管理',
    editor: '可编辑',
    viewer: '可阅读',
  }
  return m[access || 'viewer'] || '可阅读'
}

const closeUpload = async () => {
  // 关闭前主动刷新一次, 确保 image_count 立即更新
  await load()
  uploadDs.value = null
}
</script>

<template>
  <div class="page-flex">
    <!-- 页面标题 + 总数 + 新建按钮 -->
    <div class="page-header">
      <div>
        <h2 class="page-title">
          <el-icon class="page-title__icon"><Folder /></el-icon>
          <span>数据集管理</span>
          <span class="page-title__count text-faint">· {{ data.length }} 个</span>
          <!-- v3.3.2: 数据来源统计 (个人/团队共享) -->
          <span class="page-title__source">
            <!-- v3.3.3: 数据来源统计 (owner 视角: 含自己已分享给团队的 dataset) -->
            <el-tooltip
              content="包括未共享给团队的数据集, 以及您已分享给团队但仍归您所有的数据集"
              placement="top"
            >
              <el-tag size="small" type="primary" effect="plain" class="source-tag">
                <el-icon><UserFilled /></el-icon>
                个人 {{ personalTotal }}
              </el-tag>
            </el-tooltip>
            <el-tooltip
              content="其他成员共享给团队, 您作为团队成员可访问的数据集"
              placement="top"
            >
              <el-tag size="small" type="success" effect="plain" class="source-tag">
                <el-icon><Share /></el-icon>
                团队共享 {{ teamSharedTotal }}
              </el-tag>
            </el-tooltip>
          </span>
        </h2>
        <p class="page-desc text-soft">
          创建、分类、训练图像数据集; 一键启动 AI 预标注, 大幅减少人工标注工作量
        </p>
      </div>
      <el-button type="primary" :icon="Plus" @click="createOpen = true" round>
        新建数据集
      </el-button>
    </div>

    <el-table v-loading="loading" :data="pagedData" border stripe class="data-table">
      <template #empty>
        <div class="empty-state">
          <div class="empty-state__icon empty-state__icon--brand">
            <el-icon><Folder /></el-icon>
          </div>
          <div class="empty-state__title">{{ data.length === 0 ? '还没有数据集' : '没有匹配的数据集' }}</div>
          <div class="empty-state__desc">
            {{ data.length === 0
              ? '创建第一个数据集, 上传图片并启动 AI 预标注, 整个流程一键完成'
              : '尝试调整搜索关键词或清空筛选条件'
            }}
          </div>
          <div v-if="data.length === 0" class="empty-state__actions">
            <el-button type="primary" :icon="Plus" round @click="createOpen = true">
              立即创建
            </el-button>
          </div>
        </div>
      </template>
      <el-table-column type="index" :index="indexMethod" label="#" width="42" />
      <el-table-column prop="name" label="名称" min-width="160">
        <template #default="{ row }">
          <el-link type="primary" :underline="'never'" @click="openDetail(row)">
            <el-icon><Folder /></el-icon>
            {{ row.name }}
          </el-link>
        </template>
      </el-table-column>
      <el-table-column label="数据来源" width="130">
        <template #default="{ row }">
          <el-tooltip :content="getSourceMeta(row).tooltip" placement="top">
            <el-tag
              size="small"
              :type="getSourceMeta(row).type as any"
              effect="plain"
              class="source-cell"
            >
              <el-icon v-if="row?.source === 'team_shared'" style="margin-right: 3px; vertical-align: -1px;">
                <Share />
              </el-icon>
              <el-icon v-else style="margin-right: 3px; vertical-align: -1px;">
                <UserFilled />
              </el-icon>
              {{ getSourceMeta(row).label }}
            </el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="我的权限" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.source === 'team_shared' ? 'warning' : 'info'" effect="plain">
            {{ getMyAccessLabel(row.my_access) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" show-overflow-tooltip min-width="200" />
      <el-table-column prop="task_type" label="任务类型" width="120">
        <template #default="{ row }">
          <el-tooltip :content="getTaskTypeMeta(row.task_type).desc" placement="top">
            <el-tag size="small" :type="getTaskTypeMeta(row.task_type).type" effect="plain">
              <el-icon style="margin-right: 3px; vertical-align: -2px;">
                <component :is="getTaskTypeMeta(row.task_type).icon" />
              </el-icon>
              {{ getTaskTypeMeta(row.task_type).label }}
            </el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column prop="image_count" label="图片数" width="90" />
      <el-table-column prop="annotated_count" label="已标注" width="90">
        <template #default="{ row }">
          <el-tag size="small" type="success">{{ row.labeled_count || row.annotated_count || 0 }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="category_count" label="类别" width="80" />
      <el-table-column label="操作" width="520" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :icon="View" type="primary" @click="openDetail(row)">
            查看
          </el-button>
          <el-button size="small" :icon="CollectionTag" @click="openCategory(row)">类别</el-button>
          <el-button size="small" :icon="Lightning" @click="onAutoAnnotate(row)">AI 预标注</el-button>
          <el-dropdown @command="(cmd: any) => handleExport(row, cmd)">
            <el-button size="small" :icon="Download">导出</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="coco">COCO 格式</el-dropdown-item>
                <el-dropdown-item command="yolo">YOLO 格式</el-dropdown-item>
                <el-dropdown-item command="csv">CSV 明细</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button size="small" type="danger" :icon="Delete" @click="onDelete(row.id)" />
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && data.length === 0" description="还没有数据集, 点击右上角创建" />

    <!-- 分页栏: 固定在页面底部 (sticky 兜底 + flex 自然布局) -->
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

    <!-- 上传弹窗 -->
    <el-dialog
      v-model="uploadOpen"
      :title="`上传图像到「${uploadDs?.name}」`"
      width="780px"
      :close-on-click-modal="false"
      @close="closeUpload"
    >
      <UploadQueue
        :dataset-id="uploadDs?.id || null"
        @uploaded="onUpload"
      />
    </el-dialog>

    <!-- 类别管理 (v2.x: 显示各类别实时统计, 包括已确认/AI 已标/AI 候选/总样本) -->
    <el-dialog v-model="catOpen" :title="`类别管理 -「${catDs?.name}」`" width="760px" top="6vh">
      <el-input v-model="newCatName" placeholder="输入类别名称" @keyup.enter="onAddCategory">
        <template #append>
          <el-button type="primary" @click="onAddCategory">添加</el-button>
        </template>
      </el-input>

      <!-- 顶部: 4 张数据集级统计卡 (实时计算) -->
      <div v-if="categories.length > 0" class="cat-summary">
        <div class="cat-summary__card cat-summary__card--total">
          <div class="cat-summary__num">{{ categories.length }}</div>
          <div class="cat-summary__label">类别总数</div>
        </div>
        <div class="cat-summary__card cat-summary__card--human">
          <div class="cat-summary__num">{{ totalHuman }}</div>
          <div class="cat-summary__label">已确认样本</div>
        </div>
        <div class="cat-summary__card cat-summary__card--ai">
          <div class="cat-summary__num">{{ totalAi }}</div>
          <div class="cat-summary__label">AI 已标</div>
        </div>
        <div class="cat-summary__card cat-summary__card--cand">
          <div class="cat-summary__num">{{ totalCandidate }}</div>
          <div class="cat-summary__label">AI 候选</div>
        </div>
      </div>

      <!-- 类别明细表: 名称 (色块) + 4 列统计 + 总样本 -->
      <el-table :data="categories" size="small" style="margin-top: 14px;" class="cat-table">
        <el-table-column prop="id" label="ID" width="56" />
        <el-table-column prop="name" label="类别名" min-width="120">
          <template #default="{ row }">
            <div class="cat-name">
              <span class="cat-name__swatch" :style="{ background: row.color || '#409EFF' }" />
              <span class="cat-name__text">{{ row.name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="已确认" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.human_labeled_count" type="success" size="small" effect="dark">
              {{ row.human_labeled_count }}
            </el-tag>
            <span v-else class="dim">0</span>
          </template>
        </el-table-column>
        <el-table-column label="AI 已标" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.ai_labeled_count" type="primary" size="small" effect="dark">
              {{ row.ai_labeled_count }}
            </el-tag>
            <span v-else class="dim">0</span>
          </template>
        </el-table-column>
        <el-table-column label="AI 候选" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.ai_candidate_count" type="warning" size="small" effect="plain">
              {{ row.ai_candidate_count }}
            </el-tag>
            <span v-else class="dim">0</span>
          </template>
        </el-table-column>
        <el-table-column label="总样本" width="160" align="center">
          <template #default="{ row }">
            <div class="cat-total">
              <span class="cat-total__num">{{ row.sample_count || 0 }}</span>
              <el-progress
                :percentage="row.totalPct"
                :stroke-width="6"
                :show-text="false"
                :color="row.color || '#409EFF'"
                class="cat-total__bar"
              />
            </div>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="categories.length === 0" description="暂无类别" :image-size="60" />
    </el-dialog>

    <!-- 新建数据集 -->
    <el-dialog v-model="createOpen" title="新建数据集" width="560px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="createForm.name" placeholder="如：垃圾分类数据集" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="createForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="任务类型">
          <el-select v-model="createForm.task_type" class="app-select app-select--full">
            <el-option
              v-for="opt in TASK_TYPE_OPTIONS" :key="opt.value"
              :label="opt.label" :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="预置类别">
          <!-- 动态类别列表: 每项一行, 支持逐项编辑/删除; 底部增加按钮入队 -->
          <div class="preset-categories">
            <div
              v-for="(_cat, idx) in createForm.category_names"
              :key="idx"
              class="preset-categories__row"
            >
              <span class="preset-categories__index">{{ idx + 1 }}</span>
              <el-input
                v-model="createForm.category_names[idx]"
                :placeholder="idx === 0 ? '如：cat' : `类别 ${idx + 1}`"
                clearable
                class="preset-categories__input"
              />
              <el-button
                type="danger"
                :icon="Delete"
                link
                :title="`删除类别 ${idx + 1}`"
                @click="removeCategoryRow(idx)"
              />
            </div>
            <div v-if="createForm.category_names.length === 0" class="preset-categories__empty">
              暂无预置类别，点击下方"增加类别"添加
            </div>
            <el-button
              type="primary"
              :icon="Plus"
              plain
              class="preset-categories__add"
              @click="addCategoryRow"
            >
              增加类别
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" @click="onCreate">创建</el-button>
      </template>
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
}
.page-header {
  margin-bottom: 20px;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-shrink: 0;
}
.page-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
}
.page-title__icon {
  font-size: 22px;
  color: var(--brand-primary);
}
.page-title__count {
  font-size: 14px;
  font-weight: 400;
  margin-left: 4px;
}
/* v3.3.2: 数据来源统计 (个人/团队共享) */
.page-title__source {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 8px;
}
.source-tag {
  font-size: 12px;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.source-cell {
  display: inline-flex;
  align-items: center;
  cursor: default;
}
.page-desc {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.5;
}

/* 表格区: 占满剩余高度, 表格自身管滚动, 不挤压分页栏 */
.data-table {
  flex: 1 1 0;
  min-height: 0;
  /* el-table 自身是 display: table, 不接受 flex:1; 用 height: 100% 占满 */
  height: 100% !important;
  border-radius: var(--radius-md) !important;
  overflow: hidden;
}
/* 分页栏: flex 自然钉在 page-flex 底部, 配合 sticky 视觉兜底 */
.pager {
  flex-shrink: 0;
  margin-top: 12px;
  padding: 8px 0;
  display: flex;
  justify-content: flex-end;
  background: #fff;
  border-top: 1px solid #ebeef5;
  position: sticky;
  bottom: 0;
  z-index: 5;
}
/* 表格空状态: 用我们统一的 empty-state 替代 Element Plus 默认空态 */
.data-table :deep(.el-table__empty-block) {
  min-height: 320px;
  background: var(--bg-soft);
}
.data-table :deep(.el-table__empty-text) {
  line-height: 1.6;
}

/* ===========================================================
   类别管理弹窗 (v2.x): 顶部 4 张统计卡 + 类别明细
   =========================================================== */
.cat-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-top: 14px;
  padding: 12px;
  background: var(--bg-soft);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-soft);
}
.cat-summary__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 10px 8px;
  background: #fff;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-soft);
  position: relative;
  overflow: hidden;
}
.cat-summary__card::before {
  content: '';
  position: absolute;
  inset: 0 0 auto 0;
  height: 3px;
  background: var(--gradient-brand);
  opacity: 0.85;
}
.cat-summary__card--total::before { background: var(--gradient-brand); }
.cat-summary__card--human::before { background: var(--gradient-success); }
.cat-summary__card--ai::before { background: var(--gradient-cool); }
.cat-summary__card--cand::before { background: linear-gradient(135deg, #ffa940 0%, #ff7676 100%); }
.cat-summary__num {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.cat-summary__label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}

/* 类别名: 色块 + 名称 */
.cat-name { display: flex; align-items: center; gap: 8px; min-width: 0; }
.cat-name__swatch {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 3px;
  flex-shrink: 0;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.06);
}
.cat-name__text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}

/* 总样本: 数字 + 占比进度条 */
.cat-total {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.cat-total__num {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  min-width: 28px;
  text-align: right;
  font-size: 13px;
}
.cat-total__bar {
  flex: 1 1 auto;
  min-width: 0;
}
/* 表格内边距收紧, 容纳 5 列更紧凑 */
.cat-table :deep(.el-table__cell) { padding: 6px 0; }
.cat-table :deep(th.el-table__cell) {
  background: var(--bg-soft) !important;
  font-weight: 600;
}
.dim { color: var(--text-placeholder); font-size: 12px; }

/* 预置类别: 动态行布局 (序号 + 输入框 flex:1 + 删除按钮) */
.preset-categories {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.preset-categories__row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.preset-categories__index {
  flex: 0 0 24px;
  text-align: center;
  font-size: 12px;
  color: var(--text-placeholder);
  font-variant-numeric: tabular-nums;
}
.preset-categories__input {
  flex: 1 1 auto;
  min-width: 0;
}
.preset-categories__empty {
  color: var(--text-placeholder);
  font-size: 12px;
  padding: 4px 0;
}
.preset-categories__add {
  align-self: flex-start;
  margin-top: 4px;
}
</style>

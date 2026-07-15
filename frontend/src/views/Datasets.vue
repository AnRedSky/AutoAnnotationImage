<script setup lang="ts">
/**
 * Datasets.vue - 数据集列表 + 上传队列 + 类别管理 + AI 预标注 + 导出
 */
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, View, Delete, Download, CollectionTag, Lightning, Folder
} from '@element-plus/icons-vue'
import { datasetApi, autoAnnotateApi, exportApi } from '@/api'
import UploadQueue from '@/components/UploadQueue.vue'
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
const models = ref<any[]>([])

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
const createForm = ref({
  name: '',
  description: '',
  task_type: 'classification',
  category_names: ''
})

const load = async () => {
  loading.value = true
  try {
    const res: any = await datasetApi.list()
    data.value = res?.items || res || []
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

const loadCategories = async (dsId: number) => {
  try {
    const c: any = await datasetApi.categories(dsId)
    categories.value = c?.items || c || []
  } catch (e) {}
}

const loadModels = async () => {
  try {
    const ms: any = await autoAnnotateApi.models()
    models.value = ms?.models || []
  } catch {}
}

load()
loadModels()
onMounted(() => load())

watch(catDs, (v) => { if (v) loadCategories(v.id) })

const onCreate = async () => {
  const v = createForm.value
  if (!v.name) { ElMessage.warning('请输入名称'); return }
  const names = (v.category_names || '').split(/[,，\n]/).map((s: string) => s.trim()).filter(Boolean)
  try {
    await datasetApi.create({ ...v, category_names: names })
    ElMessage.success('创建成功')
    createOpen.value = false
    createForm.value = {
      name: '',
      description: '',
      task_type: 'classification',
      category_names: ''
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

const onUpload = (result: any) => {
  // UploadQueue 已 emit uploaded 事件, 这里可以刷新计数
  // 由于 UploadQueue 在弹窗内, 用户关闭时再 load
  console.log('[UploadQueue] done:', result)
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

const closeUpload = async () => {
  // 关闭前主动刷新一次, 确保 image_count 立即更新
  await load()
  uploadDs.value = null
}
</script>

<template>
  <div class="page-flex">
    <div class="page-header">
      <el-button type="primary" :icon="Plus" @click="createOpen = true">新建数据集</el-button>
      <span style="color: #909399; font-size: 13px; margin-left: 12px;">
        共 {{ data.length }} 个数据集
      </span>
    </div>

    <el-table v-loading="loading" :data="pagedData" border stripe class="data-table">
      <el-table-column type="index" :index="indexMethod" label="#" width="42" />
      <el-table-column prop="name" label="名称" min-width="160">
        <template #default="{ row }">
          <el-link type="primary" :underline="'never'" @click="openDetail(row)">
            <el-icon><Folder /></el-icon>
            {{ row.name }}
          </el-link>
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

    <!-- 类别管理 -->
    <el-dialog v-model="catOpen" :title="`类别管理 -「${catDs?.name}」`" width="500px">
      <el-input v-model="newCatName" placeholder="输入类别名称" @keyup.enter="onAddCategory">
        <template #append>
          <el-button type="primary" @click="onAddCategory">添加</el-button>
        </template>
      </el-input>
      <el-table :data="categories" size="small" style="margin-top: 16px;">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="类别名" />
        <el-table-column prop="sample_count" label="样本数" width="100">
          <template #default="{ row }">
            <el-tag size="small">{{ row.sample_count || 0 }}</el-tag>
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
          <el-select v-model="createForm.task_type" style="width: 100%;">
            <el-option
              v-for="opt in TASK_TYPE_OPTIONS" :key="opt.value"
              :label="opt.label" :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="预置类别">
          <el-input v-model="createForm.category_names" type="textarea" :rows="3"
            placeholder="多个类别用英文逗号或换行分隔，如：cat, dog, bird" />
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
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
/* 表格区: 占满剩余高度, 表格自身管滚动, 不挤压分页栏 */
.data-table {
  flex: 1 1 0;
  min-height: 0;
  /* el-table 自身是 display: table, 不接受 flex:1; 用 height: 100% 占满 */
  height: 100% !important;
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
</style>

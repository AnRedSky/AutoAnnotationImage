<script setup lang="ts">
/**
 * 团队级数据集列表 (v3.3.1 + v3.3.2 + v3.3.3)
 * ===========================================
 *
 * Props:
 *  - datasets: TeamDatasetItem[]
 *  - loading:  boolean
 *  - canManage: boolean       是否能取消共享 + 共享新数据集 (manager 角色以上)
 *  - currentUserId: number    v3.3.3: 当前登录用户 id, 用于「仅 owner 可取消共享」判断
 *
 * Emits:
 *  - view-dataset   点击跳转到 DatasetDetail
 *  - unshare        取消共享 (参数: dataset)
 *  - share-dataset  v3.3.2: 打开「共享数据集」弹窗 (manager 限定)
 */
import { DataLine, Share } from '@element-plus/icons-vue'
import type { TeamDatasetItem } from '@/api'
import { useRouter } from 'vue-router'

const props = defineProps<{
  datasets: TeamDatasetItem[]
  loading: boolean
  canManage: boolean
  currentUserId?: number | null
}>()

const emit = defineEmits<{
  (e: 'view-dataset', dataset: TeamDatasetItem): void
  (e: 'unshare', dataset: TeamDatasetItem): void
  (e: 'share-dataset'): void  // v3.3.2
}>()

const router = useRouter()

const onView = (d: TeamDatasetItem) => {
  router.push(`/datasets/${d.id}`)
}

const fmtPct = (annotated: number, total: number) => {
  if (total === 0) return '0%'
  return `${Math.round((annotated / total) * 100)}%`
}

const statusTagType = (s: string) => {
  const m: Record<string, string> = {
    draft: 'info',
    annotating: 'warning',
    training: 'primary',
    done: 'success',
  }
  return m[s] || 'info'
}

const statusLabel = (s: string) => {
  const m: Record<string, string> = {
    draft: '草稿',
    annotating: '标注中',
    training: '训练中',
    done: '已完成',
  }
  return m[s] || s
}

const taskTypeLabel = (t: string) => {
  const m: Record<string, string> = {
    classification: '分类',
    detection: '检测',
    segmentation: '分割',
  }
  return m[t] || t
}

/** v3.3.2: 角色枚举 → 中文标签 (兜底映射, 优先用后端 my_access_label) */
const myAccessLabel = (role: string) => {
  const m: Record<string, string> = {
    manager: '可管理',
    editor: '可编辑',
    viewer: '可阅读',
  }
  return m[role] || role
}

/** v3.3.3: 判断当前用户是否为该 dataset 的原始共享者 (owner) */
const isDatasetOwner = (row: TeamDatasetItem) => {
  if (props.currentUserId == null) return false
  return row.owner_id === props.currentUserId
}
</script>

<template>
  <el-card shadow="never" class="main-card">
    <div class="card-toolbar">
      <span class="title-with-icon">
        <el-icon><DataLine /></el-icon>
        <span>共享数据集 ({{ datasets.length }})</span>
      </span>
      <!-- v3.3.2: 团队管理页「共享数据集」按钮 (仅 manager) -->
      <el-button
        v-if="canManage"
        type="primary"
        size="small"
        :icon="Share"
        @click="emit('share-dataset')"
      >
        共享数据集
      </el-button>
    </div>

    <el-table
      :data="datasets"
      v-loading="loading"
      stripe
      class="data-table"
      empty-text="团队下还没有共享的数据集"
    >
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="任务类型" width="100">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ taskTypeLabel(row.task_type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="标注进度" width="180">
        <template #default="{ row }">
          <el-progress
            :percentage="Math.round((row.annotated_count / Math.max(row.image_count, 1)) * 100)"
            :stroke-width="10"
          />
          <span class="progress-label">
            {{ row.annotated_count }} / {{ row.image_count }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="类别" width="80" prop="category_count" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small" effect="plain">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="共享者" width="120">
        <template #default="{ row }">
          {{ row.owner_name }}
        </template>
      </el-table-column>
      <el-table-column label="我的权限" width="100">
        <template #default="{ row }">
          <!-- v3.3.2: 优先使用后端 my_access_label, 兜底前端映射 -->
          <el-tag
            size="small"
            :type="row.my_access === 'manager' ? 'danger' : (row.my_access === 'editor' ? 'warning' : 'info')"
            effect="plain"
          >
            {{ row.my_access_label || myAccessLabel(row.my_access) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="onView(row)">
            查看
          </el-button>
          <!-- v3.3.3: 取消共享按钮仅 owner 可见 (用户新需求 §2) -->
          <el-tooltip
            v-if="!isDatasetOwner(row)"
            content="仅数据集原始共享者可取消共享"
            placement="top"
          >
            <el-button size="small" type="danger" plain disabled>
              取消共享
            </el-button>
          </el-tooltip>
          <el-button
            v-else
            size="small"
            type="danger"
            plain
            @click="emit('unshare', row)"
          >
            取消共享
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<style scoped>
@import '@/styles/admin.css';
.card-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.title-with-icon {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}
.progress-label {
  display: block;
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 2px;
}
</style>

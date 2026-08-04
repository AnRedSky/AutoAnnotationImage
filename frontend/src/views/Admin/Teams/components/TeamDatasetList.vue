<script setup lang="ts">
/**
 * 团队级数据集列表 (v3.3.1)
 * ==========================
 *
 * Props:
 *  - datasets: TeamDatasetItem[]
 *  - loading:  boolean
 *  - canManage: boolean   是否能取消共享 (manager 角色以上)
 *
 * Emits:
 *  - view-dataset  点击跳转到 DatasetDetail
 *  - unshare       取消共享 (参数: dataset)
 */
import { DataLine } from '@element-plus/icons-vue'
import type { TeamDatasetItem } from '@/api'
import { useRouter } from 'vue-router'

defineProps<{
  datasets: TeamDatasetItem[]
  loading: boolean
  canManage: boolean
}>()

const emit = defineEmits<{
  (e: 'view-dataset', dataset: TeamDatasetItem): void
  (e: 'unshare', dataset: TeamDatasetItem): void
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
</script>

<template>
  <el-card shadow="never" class="main-card">
    <div class="card-toolbar">
      <span class="title-with-icon">
        <el-icon><DataLine /></el-icon>
        <span>共享数据集 ({{ datasets.length }})</span>
      </span>
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
          <el-tag size="small">{{ row.my_access }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="onView(row)">
            查看
          </el-button>
          <el-button
            v-if="canManage"
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

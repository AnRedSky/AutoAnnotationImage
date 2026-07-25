<script setup lang="ts">
/**
 * DatasetImageList - 图像列表视图 (el-table)
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 列: 复选框 / 缩略图 / 文件名 / 状态 / 最终类别 / AI 预测 / 尺寸 / 大小 / 上传时间 / 操作
 */
import { View, Delete, RefreshLeft } from '@element-plus/icons-vue'
import { imageApi } from '@/api'

const props = defineProps<{
  images: any[]
  statusType: (s: string) => any
  statusLabel: (s: string) => string
  confColor: (c: number) => string
  formatBytes: (b: number) => string
  hasAnnotation: (img: any) => boolean
}>()

const emit = defineEmits<{
  (e: 'rowClick', id: number): void
  (e: 'openViewer', id: number): void
  (e: 'clearAnnotation', img: any): void
  (e: 'deleteOne', img: any): void
}>()
</script>

<template>
  <el-table
    :data="images"
    border
    stripe
    class="image-list-table"
    @row-click="(row: any) => emit('rowClick', row.id)"
  >
    <el-table-column type="selection" width="48" :selectable="() => true" />
    <el-table-column label="缩略图" width="100">
      <template #default="{ row }">
        <div class="list-thumb">
          <img :src="imageApi.thumbnailUrl(row.id, 200)" :alt="row.filename" loading="lazy" />
        </div>
      </template>
    </el-table-column>
    <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
    <el-table-column prop="status" label="状态" width="120">
      <template #default="{ row }">
        <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="最终类别" width="140" show-overflow-tooltip>
      <template #default="{ row }">
        <el-tag v-if="row.final_label_name" size="small" effect="dark">{{ row.final_label_name }}</el-tag>
        <span v-else class="dim">-</span>
      </template>
    </el-table-column>
    <el-table-column label="AI 预测" min-width="160" show-overflow-tooltip>
      <template #default="{ row }">
        <span v-if="row.ai_prediction?.top1">
          <el-tag size="small">{{ row.ai_prediction.top1 }}</el-tag>
          <el-tag size="small" :color="confColor(row.ai_prediction.top1_conf || 0)"
            effect="dark" style="margin-left: 4px;">
            {{ ((row.ai_prediction.top1_conf || 0) * 100).toFixed(0) }}%
          </el-tag>
        </span>
        <span v-else class="dim">-</span>
      </template>
    </el-table-column>
    <el-table-column label="尺寸" width="100">
      <template #default="{ row }">
        <span v-if="row.width && row.height" class="dim">{{ row.width }}×{{ row.height }}</span>
        <span v-else class="dim">-</span>
      </template>
    </el-table-column>
    <el-table-column label="大小" width="90">
      <template #default="{ row }">{{ formatBytes(row.file_size) }}</template>
    </el-table-column>
    <el-table-column label="上传时间" width="170">
      <template #default="{ row }">
        <span v-if="row.created_at" class="dim">{{ row.created_at.slice(0, 16).replace('T', ' ') }}</span>
        <span v-else class="dim">-</span>
      </template>
    </el-table-column>
    <el-table-column label="操作" width="220" fixed="right">
      <template #default="{ row }">
        <el-button size="small" type="primary" :icon="View" @click.stop="emit('openViewer', row.id)">详情</el-button>
        <el-button
          v-if="hasAnnotation(row)"
          size="small" type="warning" :icon="RefreshLeft"
          @click.stop="emit('clearAnnotation', row)"
        >清除标注</el-button>
        <el-button size="small" type="danger" :icon="Delete" @click.stop="emit('deleteOne', row)" />
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.image-list-table {
  border-radius: var(--radius-md) !important;
  overflow: hidden;
}
.list-thumb {
  width: 64px;
  height: 64px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--bg-soft);
  flex-shrink: 0;
}
.list-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.dim { color: var(--text-placeholder); }
</style>

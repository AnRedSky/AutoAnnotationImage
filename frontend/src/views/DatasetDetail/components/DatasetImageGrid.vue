<script setup lang="ts">
/**
 * DatasetImageGrid - 图像网格视图
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 单图卡片内容: 缩略图 + 复选框 + 状态 tag + 详情/清除/删除 按钮 + 文件名/大小 + 最终类别/AI 预测
 */
import { View, Delete, RefreshLeft, Check } from '@element-plus/icons-vue'
import { imageApi } from '@/api'

const props = defineProps<{
  images: any[]
  selectedIds: number[]
  statusType: (s: string) => any
  statusLabel: (s: string) => string
  confColor: (c: number) => string
  formatBytes: (b: number) => string
  hasAnnotation: (img: any) => boolean
}>()

const emit = defineEmits<{
  (e: 'toggleSelect', id: number): void
  (e: 'toggleCheckbox', id: number, checked: boolean): void
  (e: 'openViewer', id: number): void
  (e: 'clearAnnotation', img: any): void
  (e: 'deleteOne', img: any): void
}>()
</script>

<template>
  <el-row :gutter="14">
    <el-col v-for="img in images" :key="img.id" :xs="12" :sm="8" :md="6" :lg="4" :xl="4">
      <el-card
        shadow="hover"
        class="image-card"
        :class="{ selected: selectedIds.includes(img.id) }"
        @click="emit('toggleSelect', img.id)"
      >
        <div class="image-thumb">
          <img
            :src="imageApi.thumbnailUrl(img.id, 320)"
            :alt="img.filename"
            loading="lazy"
            @error="(e: any) => { e.target.src = imageApi.fileUrl(img.id) }"
          />
          <el-checkbox
            class="image-checkbox"
            :model-value="selectedIds.includes(img.id)"
            @change="(v: any) => emit('toggleCheckbox', img.id, !!v)"
            @click.stop
          />
          <el-tag :type="statusType(img.status)" size="small" class="status-tag">
            {{ statusLabel(img.status) }}
          </el-tag>
          <el-button
            class="detail-btn"
            type="primary"
            :icon="View"
            size="small"
            circle
            @click.stop="emit('openViewer', img.id)"
          />
          <el-tooltip
            v-if="hasAnnotation(img)"
            content="清除标注" placement="top"
          >
            <el-button
              class="clear-btn"
              type="warning"
              :icon="RefreshLeft"
              size="small"
              circle
              @click.stop="emit('clearAnnotation', img)"
            />
          </el-tooltip>
          <el-button class="del-btn" type="danger" :icon="Delete" size="small" circle
            @click.stop="emit('deleteOne', img)" />
        </div>
        <div class="image-info">
          <el-tooltip :content="img.filename" placement="top">
            <div class="image-name">{{ img.filename }}</div>
          </el-tooltip>
          <div class="image-meta">
            <span>{{ formatBytes(img.file_size) }}</span>
            <span v-if="img.width && img.height">{{ img.width }}×{{ img.height }}</span>
          </div>
          <div v-if="img.final_label_name" class="image-label">
            <el-tag size="small" effect="dark">{{ img.final_label_name }}</el-tag>
          </div>
          <div v-else-if="img.ai_prediction?.top1" class="image-ai">
            <span class="image-ai-prefix">AI:</span>
            <el-tag size="small">{{ img.ai_prediction.top1 }}</el-tag>
            <el-tag size="small" :color="confColor(img.ai_prediction.top1_conf || 0)"
              effect="dark" style="margin-left: 4px;">
              {{ ((img.ai_prediction.top1_conf || 0) * 100).toFixed(0) }}%
            </el-tag>
          </div>
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.image-card {
  margin-bottom: 12px;
  cursor: pointer;
  transition: all 0.2s var(--ease-out);
  user-select: none;
  border-radius: var(--radius-md) !important;
  overflow: hidden;
}
.image-card.selected {
  border-color: var(--brand-primary) !important;
  box-shadow: 0 0 0 2px rgba(79, 124, 255, 0.3) !important;
}
.image-card:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-md) !important;
}
.image-thumb {
  position: relative;
  width: 100%;
  aspect-ratio: 1 / 1;
  background: linear-gradient(135deg, #f5f7 0%, #ebedf2 100%);
  border-radius: 4px;
  overflow: hidden;
}
.image-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.4s var(--ease-out);
}
.image-card:hover .image-thumb img {
  transform: scale(1.05);
}
.image-checkbox {
  position: absolute;
  top: 6px;
  left: 6px;
  background: rgba(255, 255, 255, 0.9);
  border-radius: 4px;
  padding: 0 4px;
  backdrop-filter: blur(4px);
}
.status-tag {
  position: absolute;
  top: 6px;
  right: 6px;
  font-weight: 500;
  backdrop-filter: blur(4px);
}
.del-btn {
  position: absolute;
  bottom: 6px;
  right: 6px;
  opacity: 0;
  transition: opacity 0.2s var(--ease-out);
}
.clear-btn {
  position: absolute;
  bottom: 6px;
  left: 32px;
  opacity: 0;
  transition: opacity 0.2s var(--ease-out);
}
.image-card:hover .del-btn { opacity: 1; }
.image-card:hover .clear-btn { opacity: 1; }

.detail-btn {
  position: absolute;
  top: 32px;
  right: 6px;
}

.image-info {
  padding: 10px 4px 0;
  font-size: 12px;
}
.image-name {
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
}
.image-meta {
  display: flex;
  justify-content: space-between;
  color: var(--text-placeholder);
  margin-top: 2px;
  font-size: 11px;
}
.image-label, .image-ai { margin-top: 6px; }
.image-ai { display: flex; align-items: center; gap: 4px; }
.image-ai-prefix { font-size: 11px; color: #909399; }
</style>

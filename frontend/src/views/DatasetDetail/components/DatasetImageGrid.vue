<script setup lang="ts">
/**
 * DatasetImageGrid - 图像网格视图 (多列卡片布局)
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 * v3.x 适配嵌套容器: 本组件直接渲染到父级 .ds-image-area 滚动容器内
 *   - 不再需要自身限定高度, 由父容器统一管理 overflow
 *   - 选择/操作事件与 DatasetImageList 完全对齐, 父级可无差别切换视图
 *
 * 单图卡片内容: 缩略图 + 复选框 + 状态 tag + 详情/清除/删除 按钮 + 文件名/大小 + 最终类别/AI 预测
 */
import { View, Delete, RefreshLeft, Check } from '@element-plus/icons-vue'
import { imageApi } from '@/api'
import { getRejectReasonLabel } from '@/utils/rejectReason'  // v3.0.0 新增
import { computed } from 'vue'

const props = defineProps<{
  images: any[]
  selectedIds: number[]
  statusType: (s: string) => any
  statusLabel: (s: string) => string
  confColor: (c: number) => string
  formatBytes: (b: number) => string
  hasAnnotation: (img: any) => boolean
  /** v3.x: 网格尺寸 (small/medium/large) - 控制每行卡片数 */
  gridSize?: 'small' | 'medium' | 'large'
}>()

const emit = defineEmits<{
  (e: 'toggleSelect', id: number): void
  (e: 'toggleCheckbox', id: number, checked: boolean): void
  (e: 'openViewer', id: number): void
  (e: 'clearAnnotation', img: any): void
  (e: 'deleteOne', img: any): void
  (e: 'unmarkUnqualified', img: any): void  // v3.0.0 新增
}>()

/**
 * v3.x: 根据 gridSize 动态计算 el-col 响应式 span
 * - large: 大卡片, 每行少几张 (xs=12 sm=8 md=8 lg=6 xl=4)
 * - medium: 默认, 平衡展示 (xs=12 sm=8 md=6 lg=4 xl=4)
 * - small: 小卡片, 每行多几张 (xs=8 sm=6 md=4 lg=3 xl=3)
 * - el-col 的 span 数字代表占 24 栅格的格数
 */
const colSpans = computed(() => {
  switch (props.gridSize || 'medium') {
    case 'large':
      return { xs: 12, sm: 8, md: 8, lg: 6, xl: 4 }
    case 'small':
      return { xs: 8, sm: 6, md: 4, lg: 3, xl: 3 }
    case 'medium':
    default:
      return { xs: 12, sm: 8, md: 6, lg: 4, xl: 4 }
  }
})

/** v3.x: 根据 gridSize 调整缩略图后端请求尺寸 (减少小图的带宽浪费) */
const thumbnailSize = computed(() => {
  switch (props.gridSize || 'medium') {
    case 'large': return 480  // 大图请求更高清
    case 'small': return 160  // 小图降低请求尺寸
    case 'medium':
    default:      return 320
  }
})
</script>

<template>
  <el-row :gutter="14">
    <el-col
      v-for="img in images"
      :key="img.id"
      :xs="colSpans.xs"
      :sm="colSpans.sm"
      :md="colSpans.md"
      :lg="colSpans.lg"
      :xl="colSpans.xl"
    >
      <el-card
        shadow="hover"
        class="image-card"
        :class="[
          { selected: selectedIds.includes(img.id) },
          `image-card--${gridSize || 'medium'}`,
        ]"
        @click="emit('toggleSelect', img.id)"
      >
        <div class="image-thumb">
          <img
            :src="imageApi.thumbnailUrl(img.id, thumbnailSize)"
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
          <!-- v3.0.0: 不合格图片红色覆盖层 + 角标 -->
          <div v-if="img.quality_flag === 'unqualified'" class="unqualified-overlay">
            <el-tag type="danger" size="small" effect="dark" class="unqualified-badge">
              不合格
            </el-tag>
            <el-tag type="danger" size="small" effect="plain" class="unqualified-reason">
              {{ getRejectReasonLabel(img.reject_reason) }}
            </el-tag>
          </div>
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
          <!-- v3.0.0: 撤销不合格标记按钮 (仅不合格图显示, hover 可见) -->
          <el-tooltip
            v-if="img.quality_flag === 'unqualified'"
            content="撤销不合格标记" placement="top"
          >
            <el-button
              class="unmark-btn"
              type="success"
              :icon="RefreshLeft"
              size="small"
              circle
              @click.stop="emit('unmarkUnqualified', img)"
            />
          </el-tooltip>
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
.image-card:hover .unmark-btn { opacity: 1; }

/* v3.0.0: 不合格图片视觉标记 */
.image-card:has(.unqualified-overlay) {
  border-color: #f56c6c !important;
}
.unqualified-overlay {
  position: absolute;
  inset: 0;
  background: rgba(245, 108, 108, 0.18);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  pointer-events: none;
  border-radius: 4px;
}
.unqualified-badge {
  font-size: 13px;
  font-weight: 600;
}
.unqualified-reason {
  font-size: 11px;
}
.unmark-btn {
  position: absolute;
  bottom: 6px;
  left: 64px;
  opacity: 0;
  transition: opacity 0.2s var(--ease-out);
}

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

/* v3.x: 网格尺寸差异化样式
 * - .image-card--small: 小卡片, 信息区更紧凑, 部分元数据隐藏
 * - .image-card--medium: 默认
 * - .image-card--large: 大卡片, 信息更突出, 字号略大
 */
.image-card--small .image-info { padding: 6px 2px 0; }
.image-card--small .image-name { font-size: 12px; }
.image-card--small .image-meta { font-size: 10px; }
.image-card--small .image-label,
.image-card--small .image-ai { margin-top: 4px; }

.image-card--large .image-info { padding: 12px 6px 0; }
.image-card--large .image-name { font-size: 14px; }
.image-card--large .image-meta { font-size: 12px; margin-top: 4px; }
.image-card--large .image-label,
.image-card--large .image-ai { margin-top: 8px; }
</style>

<script setup lang="ts">
/**
 * PreviewList - 测评结果列表 (DatasetDetail 弹窗内 Tab 内容)
 * 三列布局: 缩略图 / 文件名 + top-1 预测 + 置信度 / 原因 tag
 * 接收 items: Array<{image_id, filename, thumb_url, top1, top1_conf, candidates, reason, would_label, ...}>
 */
import { Picture, CircleCheck, Warning, InfoFilled } from '@element-plus/icons-vue'
import { imageApi } from '@/api'

defineProps<{
  items: any[]
}>()

function thumbSrc(item: any): string {
  return imageApi.thumbnailUrl(item.image_id, 96)
}

function reasonLabel(reason: string): string {
  return {
    would_label: '≥ 阈值, 会被自动标注',
    below_threshold: '低于阈值, 保留待人工',
    not_in_categories: '不在项目类目',
    no_match: '模型输出与项目类目无交集',
  }[reason] || reason
}

function reasonType(reason: string): 'success' | 'warning' | 'info' | 'danger' {
  const t: Record<string, 'success' | 'warning' | 'info' | 'danger'> = {
    would_label: 'success',
    below_threshold: 'warning',
    not_in_categories: 'info',
    no_match: 'danger',
  }
  return t[reason] || 'info'
}

function reasonIcon(reason: string) {
  return {
    would_label: CircleCheck,
    below_threshold: Warning,
    not_in_categories: InfoFilled,
    no_match: Warning,
  }[reason] || InfoFilled
}
</script>

<template>
  <div v-if="!items.length" class="preview-list__empty">
    <el-icon class="preview-list__empty-icon"><Picture /></el-icon>
    <div>该分组无图片</div>
  </div>
  <div v-else class="preview-list">
    <div v-for="item in items" :key="item.image_id" class="preview-list__row">
      <div class="preview-list__thumb">
        <img
          :src="thumbSrc(item)" :alt="item.filename"
          loading="lazy"
          @error="(e: any) => { (e.target as HTMLImageElement).style.opacity = '0.3' }"
        />
      </div>
      <div class="preview-list__main">
        <div class="preview-list__filename" :title="item.filename">{{ item.filename }}</div>
        <div class="preview-list__pred">
          <template v-if="item.top1">
            <el-tag size="small" effect="plain" type="primary">{{ item.top1 }}</el-tag>
            <span class="preview-list__conf">
              置信度 <b :class="`preview-list__conf-num preview-list__conf-num--${reasonType(item.reason)}`">
                {{ (item.top1_conf * 100).toFixed(1) }}%
              </b>
            </span>
            <el-tag
              v-if="!item.in_project_categories"
              size="small" type="info" effect="plain" style="margin-left: 4px;"
            >不在项目类目</el-tag>
          </template>
          <template v-else>
            <span class="preview-list__no-pred">— 无预测 —</span>
          </template>
        </div>
        <!-- Top-3 候选: 帮用户判断 top-1 不在项目类目时, 次优是否在 -->
        <div v-if="item.candidates && item.candidates.length > 1" class="preview-list__cands">
          <span
            v-for="(c, i) in item.candidates.slice(0, 3)" :key="i"
            class="preview-list__cand"
          >
            <span class="preview-list__cand-label">{{ c.label }}</span>
            <span class="preview-list__cand-conf">{{ (c.conf * 100).toFixed(1) }}%</span>
          </span>
        </div>
      </div>
      <div class="preview-list__reason">
        <el-tag :type="reasonType(item.reason)" size="small" effect="dark">
          <el-icon style="margin-right: 2px;"><component :is="reasonIcon(item.reason)" /></el-icon>
          {{ reasonLabel(item.reason) }}
        </el-tag>
      </div>
    </div>
  </div>
</template>

<style scoped>
.preview-list {
  max-height: 480px;
  overflow-y: auto;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  background: var(--bg-card);
}
.preview-list__row {
  display: grid;
  grid-template-columns: 72px 1fr 200px;
  gap: 12px;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-soft);
}
.preview-list__row:last-child { border-bottom: none; }
.preview-list__row:hover { background: rgba(64, 158, 255, 0.04); }

.preview-list__thumb {
  width: 72px;
  height: 72px;
  border-radius: 6px;
  overflow: hidden;
  background: #f5f7fa;
  display: flex;
  align-items: center;
  justify-content: center;
}
.preview-list__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: opacity 0.2s;
}

.preview-list__main { min-width: 0; }
.preview-list__filename {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 4px;
}
.preview-list__pred {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  flex-wrap: wrap;
}
.preview-list__conf-num { font-weight: 600; }
.preview-list__conf-num--success { color: #67c23a; }
.preview-list__conf-num--warning { color: #e6a23c; }
.preview-list__conf-num--info    { color: #909399; }
.preview-list__conf-num--danger  { color: #f56c6c; }
.preview-list__no-pred { color: var(--text-placeholder); font-style: italic; }

.preview-list__cands {
  margin-top: 4px;
  display: flex;
  gap: 6px;
  font-size: 11px;
}
.preview-list__cand {
  background: #f5f7fa;
  border-radius: 4px;
  padding: 2px 6px;
  color: var(--text-secondary);
}
.preview-list__cand-label { margin-right: 4px; }
.preview-list__cand-conf { font-weight: 600; color: var(--text-primary); }

.preview-list__reason {
  text-align: right;
}

.preview-list__empty {
  height: 200px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--text-placeholder);
  border: 1px dashed var(--border-soft);
  border-radius: 8px;
  background: var(--bg-card);
}
.preview-list__empty-icon {
  font-size: 36px;
  margin-bottom: 8px;
  opacity: 0.5;
}
</style>

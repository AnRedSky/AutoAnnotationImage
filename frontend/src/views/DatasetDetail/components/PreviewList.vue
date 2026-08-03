<script setup lang="ts">
/**
 * PreviewList - 测评结果列表 (DatasetDetail 弹窗内 Tab 内容)
 * ===========================================================
 * v2.5.46: 多任务渲染 (classification / detection / segmentation)
 * - classification: top-1 标签 + 置信度 + top-3 候选 + 原因 tag
 * - detection:     bbox 计数 + max_conf + bbox 类别列表 + 原因 tag
 * - segmentation:  max_softmax 置信度 + 原因 tag
 *
 * 三种数据契约均出自 backend/app/api/image.py:preview_confidence
 * 由 props.taskType 决定渲染分支
 */
import { Picture, CircleCheck, Warning, InfoFilled, Aim, Brush } from '@element-plus/icons-vue'
import { imageApi } from '@/api'

const props = defineProps<{
  items: any[]
  /** v2.5.46: 数据集任务类型, 决定渲染分支; 缺省 classification 向后兼容 */
  taskType?: 'classification' | 'detection' | 'segmentation'
}>()

const taskType = () => props.taskType || 'classification'

function thumbSrc(item: any): string {
  return imageApi.thumbnailUrl(item.image_id, 96)
}

/** 通用 reason 文案/类型/图标 — 三任务共享 */
function reasonLabel(reason: string): string {
  return {
    would_label: '≥ 阈值, 会被自动标注',
    below_threshold: '低于阈值, 保留待人工',
    not_in_categories: '不在项目类目',
    no_match: '模型输出与项目类目无交集',
    no_bbox: '未检测到目标, 保留待人工',
    infer_failed: '推理失败, 保留待人工',
  }[reason] || reason
}

function reasonType(reason: string): 'success' | 'warning' | 'info' | 'danger' {
  const t: Record<string, 'success' | 'warning' | 'info' | 'danger'> = {
    would_label: 'success',
    below_threshold: 'warning',
    not_in_categories: 'info',
    no_match: 'danger',
    no_bbox: 'warning',
    infer_failed: 'warning',
  }
  return t[reason] || 'info'
}

function reasonIcon(reason: string) {
  return {
    would_label: CircleCheck,
    below_threshold: Warning,
    not_in_categories: InfoFilled,
    no_match: Warning,
    no_bbox: Aim,
    infer_failed: Warning,
  }[reason] || InfoFilled
}

/** 任务类型标签 */
function taskTypeLabel(): string {
  return {
    classification: '分类',
    detection: '检测',
    segmentation: '分割',
  }[taskType()] || '分类'
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

        <!-- ========== classification ========== -->
        <div v-if="taskType() === 'classification'" class="preview-list__pred">
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
          <template v-else-if="item.reason === 'infer_failed'">
            <!-- v3.4.1 P1-3: 推理失败展示后端 error 字段 -->
            <span class="preview-list__no-pred">— 推理失败 —</span>
            <el-tooltip
              v-if="item.error"
              :content="item.error"
              placement="top"
            >
              <el-icon class="preview-list__error-icon"><Warning /></el-icon>
            </el-tooltip>
          </template>
          <template v-else>
            <span class="preview-list__no-pred">— 无预测 —</span>
          </template>
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

        <!-- ========== detection ========== -->
        <div v-else-if="taskType() === 'detection'" class="preview-list__pred">
          <el-tag v-if="(item.bbox_count || 0) > 0" size="small" type="warning" effect="plain">
            <el-icon style="margin-right: 2px;"><Aim /></el-icon>
            {{ item.bbox_count }} 个检测框
          </el-tag>
          <el-tag v-else size="small" type="info" effect="plain">无目标</el-tag>
          <span class="preview-list__conf">
            最高置信度 <b :class="`preview-list__conf-num preview-list__conf-num--${reasonType(item.reason)}`">
              {{ ((item.max_conf || 0) * 100).toFixed(1) }}%
            </b>
          </span>
          <el-tag
            v-if="(item.in_categories_count || 0) === 0 && (item.bbox_count || 0) > 0"
            size="small" type="info" effect="plain" style="margin-left: 4px;"
          >类目不在项目内</el-tag>
          <!-- 前 3 个 bbox 类别摘要 -->
          <div v-if="item.bboxes && item.bboxes.length > 0" class="preview-list__cands">
            <span
              v-for="(b, i) in item.bboxes.slice(0, 3)" :key="i"
              class="preview-list__cand"
              :class="{ 'preview-list__cand--off': !b.in_project_categories }"
            >
              <span class="preview-list__cand-label">{{ b.class_name }}</span>
              <span class="preview-list__cand-conf">{{ (b.confidence * 100).toFixed(1) }}%</span>
            </span>
            <span v-if="item.bboxes.length > 3" class="preview-list__cand-more">
              +{{ item.bboxes.length - 3 }}
            </span>
          </div>
        </div>

        <!-- ========== segmentation ========== -->
        <div v-else-if="taskType() === 'segmentation'" class="preview-list__pred">
          <el-tag size="small" type="success" effect="plain">
            <el-icon style="margin-right: 2px;"><Brush /></el-icon>
            像素级 mask
          </el-tag>
          <span class="preview-list__conf">
            最大 softmax <b :class="`preview-list__conf-num preview-list__conf-num--${reasonType(item.reason)}`">
              {{ ((item.max_conf || 0) * 100).toFixed(1) }}%
            </b>
          </span>
          <span class="preview-list__seg-hint">
            ≥ 阈值即落标, mask 已写入待人工精修
          </span>
        </div>
      </div>
      <div class="preview-list__reason">
        <el-tag :type="reasonType(item.reason)" size="small" effect="dark">
          <el-icon style="margin-right: 2px;"><component :is="reasonIcon(item.reason)" /></el-icon>
          {{ reasonLabel(item.reason) }}
        </el-tag>
        <div class="preview-list__task-type">{{ taskTypeLabel() }}</div>
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
.preview-list__error-icon {
  color: #f56c6c;
  cursor: help;
  margin-left: 4px;
  font-size: 14px;
  vertical-align: middle;
}

.preview-list__cands {
  margin-top: 4px;
  display: flex;
  gap: 6px;
  font-size: 11px;
  align-items: center;
}
.preview-list__cand {
  background: #f5f7fa;
  border-radius: 4px;
  padding: 2px 6px;
  color: var(--text-secondary);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.preview-list__cand--off {
  opacity: 0.55;
  background: #fef0f0;
}
.preview-list__cand-label { margin-right: 4px; }
.preview-list__cand-conf { font-weight: 600; color: var(--text-primary); }
.preview-list__cand-more {
  font-size: 11px;
  color: var(--text-placeholder);
  padding: 0 4px;
}
.preview-list__seg-hint {
  font-size: 11px;
  color: var(--text-placeholder);
  font-style: italic;
}

.preview-list__reason {
  text-align: right;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}
.preview-list__task-type {
  font-size: 10.5px;
  color: var(--text-placeholder);
  letter-spacing: 0.4px;
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

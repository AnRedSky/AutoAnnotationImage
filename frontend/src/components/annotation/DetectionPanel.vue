<!--
  DetectionPanel.vue (v2.5.7 拆分自 Annotate.vue)
  ===============================================
  目标检测任务右侧操作面板 (5 sections + el-popover)

  v2.5.6 关键设计:
  - 类别下拉已迁移到 Section 5 el-popover (点 el-tag 弹出改类别)
  - 删除按钮: el-tag × 按钮承担 (走 confirmAndRemove)
  - 移除原 Section 2「选中 bbox」

  父组件 (Annotate.vue) 通过 v-bind 传入所有 props, 通过 emit 抛出用户操作.

  Props (业务通用, 弱耦合):
    detAnnotRef:        ref<any> 子组件模板 ref
    bboxList:           当前 bbox 列表
    detDirty:           dirty 状态 (本地 ref, 父组件同步)
    annotatorSaving:    保存中 loading
    sortedCategories:   类别列表 (按 id 升序)
    copySuggestions:    跨图复制建议
    copySuggestionSourceCount: 跨图复制来源数
    canGoPrev:          是否能上一张
    noMore:             是否已到末尾
    historyCursor:      历史栈游标
    historyIds:         历史栈
    image:              当前图
    detOpenPopoverIdx:  当前打开 popover 的 bbox idx (同时只能一个)

  Emits:
    undo, redo, clear-draft, save, cancel
    apply-copy-suggestions, ignore-copy-suggestions
    prev, next, view-dataset
    tag-click(idx), category-change(idx, catId), popover-visible-change(idx, v)
-->
<template>
  <el-card title="检测操作面板">
    <!-- 1. 工具与历史 -->
    <div class="op-section">
      <div class="op-section-title">1. 工具与历史</div>
      <div style="display: flex; gap: 4px; margin-top: 6px;">
        <el-button
          size="small" :icon="RefreshLeft"
          style="flex: 1;"
          :disabled="!detAnnotRef?.canUndo?.value"
          @click="emit('undo')"
        >撤销 (Ctrl+Z)</el-button>
        <el-button
          size="small" :icon="RefreshRight"
          style="flex: 1;"
          :disabled="!detAnnotRef?.canRedo?.value"
          @click="emit('redo')"
        >重做 (Ctrl+Y)</el-button>
      </div>
      <!-- 目标类型选择 (始终显示, 画新 bbox 时使用) -->
      <div style="margin-top: 8px;">
        <div style="font-size: 11px; color: #909399; margin-bottom: 4px;">
          目标类型 <span style="color: #67c23a;">(画新 bbox 时使用)</span>
        </div>
        <el-select
          :model-value="detAnnotRef?.defaultCategoryId?.value ?? null"
          @update:model-value="onTargetCategoryChange"
          placeholder="选择目标类型" size="small"
          style="width: 100%;" filterable
        >
          <el-option
            v-for="c in sortedCategories" :key="c.id" :value="c.id" :label="c.name"
          >
            <span class="cat-dot" :style="{ background: catColor(c.id) }"></span>
            {{ c.name }}
          </el-option>
        </el-select>
      </div>
      <el-button
        size="small" type="warning" plain
        style="margin-top: 8px; width: 100%;"
        @click="emit('clear-draft')"
      >清空未保存</el-button>
      <div style="margin-top: 8px; padding: 6px 8px; background: #f0f9ff; border-left: 3px solid #409eff; border-radius: 3px; font-size: 11px; color: #606266; line-height: 1.6;">
        <div><strong>💡 智能标注</strong> (无需切换模式):</div>
        <div>• 拖空白处 → 画新 bbox</div>
        <div>• 点 bbox → 选中 (出现 8 handle)</div>
        <div>• 拖 body → 平移, 拖 8 handle → 缩放</div>
        <div>• <kbd>Delete</kbd> 删除选中 / 点 <kbd>×</kbd> 删除对应</div>
      </div>
    </div>

    <!-- 2. 智能建议 (条件性显示) -->
    <el-alert
      v-if="copySuggestions.length > 0"
      type="info" :closable="true" show-icon
      style="margin-bottom: 12px;"
      @close="emit('ignore-copy-suggestions')"
    >
      <template #title>
        <div style="font-size: 12px; line-height: 1.6;">
          <strong>2. 智能建议</strong>: 基于同数据集 {{ copySuggestionSourceCount }} 张已标注图,
          <strong>{{ copySuggestions.length }}</strong> 个类别可复制
        </div>
      </template>
      <div style="margin-top: 6px;">
        <el-tag
          v-for="s in copySuggestions" :key="s.category_id"
          size="small" type="info" effect="plain"
          style="margin-right: 4px; margin-bottom: 4px;"
        >
          {{ catName(s.category_id) }} ×{{ s.source_count }}
        </el-tag>
        <div style="margin-top: 8px; display: flex; gap: 6px;">
          <el-button
            type="primary" size="small" :icon="MagicStick"
            @click="emit('apply-copy-suggestions')"
          >应用建议</el-button>
          <el-button size="small" text @click="emit('ignore-copy-suggestions')">忽略</el-button>
        </div>
      </div>
    </el-alert>

    <!-- 3. 图片导航 -->
    <div class="op-section">
      <div class="op-section-title">3. 图片导航</div>
      <div style="display: flex; gap: 8px; margin-top: 6px;">
        <el-button
          style="flex: 1;" :icon="ArrowLeft"
          :disabled="!canGoPrev"
          @click="emit('prev')"
        >上一张 (P)</el-button>
        <el-button
          style="flex: 1;"
          :type="noMore ? 'info' : 'primary'"
          :plain="!noMore"
          :disabled="noMore"
          @click="emit('next')"
        >{{ noMore ? '已是最后一张' : '下一张 (N)' }}</el-button>
      </div>
      <div v-if="image" style="margin-top: 6px; font-size: 12px; color: #909399; text-align: center;">
        {{ historyCursor + 1 }} / {{ historyIds.length || '?' }} · <strong>{{ image.filename }}</strong>
      </div>
    </div>

    <!-- 4. 当前 bbox 列表 (类别下拉已迁移到每个 el-tag 的 el-popover 内) -->
    <div class="op-section">
      <div class="op-section-title">
        4. 当前 bbox ({{ bboxList.length }})
      </div>
      <el-empty v-if="bboxList.length === 0" description="尚未画任何 bbox" :image-size="50" />
      <div v-else style="margin-top: 6px; max-height: 180px; overflow-y: auto;">
        <el-popover
          v-for="(b, idx) in bboxList" :key="b.id || idx"
          :visible="detOpenPopoverIdx === idx"
          placement="bottom-start"
          :width="220"
          trigger="manual"
          :show-arrow="false"
          :hide-after="0"
          @update:visible="(v: boolean) => emit('popover-visible-change', idx, v)"
        >
          <template #reference>
            <el-tag
              size="small" effect="plain"
              :type="detAnnotRef?.selectedIndex?.value === idx ? 'primary' : 'info'"
              style="margin: 2px 4px 2px 0; cursor: pointer;"
              @click="emit('tag-click', idx)"
              closable
              @close.stop="emit('remove-bbox', idx)"
            >
              <span class="cat-dot" :style="{ background: catColor(b.category_id) }"></span>
              #{{ idx + 1 }} {{ catName(b.category_id) }}
            </el-tag>
          </template>
          <div class="det-cat-popover">
            <div class="det-cat-popover-title">变更 #{{ idx + 1 }} 类别</div>
            <el-select
              :model-value="b.category_id"
              @update:model-value="(v: number | null) => emit('category-change', idx, v)"
              size="small" style="width: 100%;" filterable
            >
              <el-option
                v-for="c in sortedCategories" :key="c.id" :value="c.id" :label="c.name"
              >
                <span class="cat-dot" :style="{ background: catColor(c.id) }"></span>
                {{ c.name }}
              </el-option>
            </el-select>
          </div>
        </el-popover>
      </div>
    </div>

    <!-- 5. 提交 -->
    <div class="op-section">
      <div class="op-section-title">5. 提交</div>
      <div style="display: flex; gap: 8px; margin-top: 6px;">
        <el-button
          type="primary"
          :icon="Check"
          :disabled="!detDirty || bboxList.length === 0 || annotatorSaving"
          :loading="annotatorSaving"
          style="flex: 1;"
          @click="emit('save')"
        >保存 ({{ bboxList.length }})</el-button>
        <el-button
          :icon="Close" style="flex: 1;"
          :disabled="!detDirty || annotatorSaving"
          @click="emit('cancel')"
        >取消</el-button>
      </div>
      <div v-if="detDirty" style="margin-top: 4px; font-size: 11px; color: #e6a23c;">
        ● 有未保存的修改
      </div>
    </div>

    <div class="op-section">
      <el-link type="primary" :icon="View" @click="emit('view-dataset')">
        去数据集详情浏览全部图片
      </el-link>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { Check, Close, ArrowLeft, View, MagicStick, RefreshLeft, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

interface Category { id: number; name: string }
interface BBox {
  id?: number
  x_min: number; y_min: number; x_max: number; y_max: number
  category_id: number; confidence?: number
}
interface CopySuggestion {
  category_id: number
  avg_x_min: number; avg_y_min: number
  avg_x_max: number; avg_y_max: number
  source_count: number
}
interface Image {
  id: number
  filename: string
  width: number
  height: number
}

const props = defineProps<{
  detAnnotRef: any
  bboxList: BBox[]
  detDirty: boolean
  annotatorSaving: boolean
  sortedCategories: Category[]
  copySuggestions: CopySuggestion[]
  copySuggestionSourceCount: number
  canGoPrev: boolean
  noMore: boolean
  historyCursor: number
  historyIds: number[]
  image: Image | null
  detOpenPopoverIdx: number | null
}>()

const emit = defineEmits<{
  (e: 'undo'): void
  (e: 'redo'): void
  (e: 'clear-draft'): void
  (e: 'save'): void
  (e: 'cancel'): void
  (e: 'apply-copy-suggestions'): void
  (e: 'ignore-copy-suggestions'): void
  (e: 'prev'): void
  (e: 'next'): void
  (e: 'view-dataset'): void
  (e: 'tag-click', idx: number): void
  (e: 'category-change', idx: number, catId: number | null): void
  (e: 'popover-visible-change', idx: number, v: boolean): void
  (e: 'remove-bbox', idx: number): void
  (e: 'target-category-change', catId: number | null): void
}>()

// 调色板 (与 DetectionAnnotator 一致, 保证 bbox 颜色一致)
const DET_PALETTE = [
  '#f56c6c', '#67c23a', '#409eff', '#e6a23c',
  '#909399', '#9b59b6', '#1abc9c', '#ff5722',
]
function catColor(catId: number | null | undefined): string {
  if (catId == null) return '#909399'
  return DET_PALETTE[Math.abs(Number(catId)) % DET_PALETTE.length]
}
function catName(catId: number | null | undefined): string {
  if (catId == null) return ''
  const c = props.sortedCategories.find((x) => x.id === catId)
  return c ? c.name : `cls_${catId}`
}

function onTargetCategoryChange(catId: number | null) {
  emit('target-category-change', catId)
  // 同步给子组件 (直接更新 ref.value, 但 ref 是父组件传入的)
  // 父组件通过监听 emit 后调用子组件方法
}
</script>

<style scoped>
.op-section {
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px dashed #ebeef5;
}
.op-section:last-child {
  margin-bottom: 0;
  padding-bottom: 0;
  border-bottom: none;
}
.op-section-title {
  font-size: 12px;
  font-weight: 600;
  color: #606266;
  letter-spacing: 0.5px;
  display: flex;
  align-items: center;
}
.op-section-title::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 12px;
  background: #409eff;
  margin-right: 6px;
  border-radius: 2px;
}
.cat-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}
.det-cat-popover {
  padding: 4px 0;
}
.det-cat-popover-title {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
  letter-spacing: 0.3px;
}
</style>

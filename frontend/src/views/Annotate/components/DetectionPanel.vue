<!--
  DetectionPanel.vue (v2.5.7 拆分自 Annotate.vue, v2.5.11 精简文本)
  ===============================================
  目标检测任务右侧操作面板 (5 sections + el-popover)

  v2.5.6 关键设计:
  - 类别下拉已迁移到 Section 4 el-popover (点 el-tag 弹出改类别)
  - 删除按钮: el-tag × 按钮承担 (走 confirmAndRemove)
  - 移除原 Section 2「选中 bbox」

  v2.5.14 精简:
  - 移除 Section 1 末尾的「清空未保存」按钮 (v2.5.14: 用 undoAll + clearAllWithConfirm 替代, 见 Section 5 重做按钮)
  - 移除 Section 5 提交后的「取消」按钮 (v2.5.14: 用撤销按钮替代, 一键回到 last saved 状态)
  - 撤销按钮语义升级: 一键撤销本次所有修改 (替代取消)
  - 重做按钮语义升级: 清空全部标注带弹窗确认 (替代清空未保存)

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
  <el-card class="op-card" title="检测操作面板">
    <!-- 1. 工具与历史
         v2.5.14 调整:
         - 撤销按钮: 一键撤销本次所有修改 (替代原"取消"按钮)
         - 重做按钮: 清空全部标注 (弹窗确认, 替代原"清空未保存"按钮)
         - 移除原"清空未保存"按钮 (合并到重做按钮) -->
    <div class="op-section">
      <div class="op-section-title">1. 工具与历史</div>
      <div style="display: flex; gap: 4px; margin-top: 6px;">
        <el-button
          size="small" :icon="RefreshLeft"
          style="flex: 1;"
          :disabled="!detDirty"
          @click="detAnnotRef?.undoAll?.()"
        >撤销本次修改</el-button>
        <el-button
          size="small" :icon="RefreshRight"
          style="flex: 1;"
          :disabled="bboxList.length === 0"
          @click="detAnnotRef?.clearAllWithConfirm?.()"
        >清空全部</el-button>
      </div>
      <!-- 目标类型选择 (画新 bbox 时使用)
           v2.5.13 修复: 移除 ?.value
           · detAnnotRef.defaultCategoryId 在 Vue 3 defineExpose 已被自动解包, 不能再加 .value
           · 原写法 detAnnotRef?.defaultCategoryId?.value 永远拿到 undefined
           · 导致 el-select 的 model-value 始终是 null, 选完类别不显示选中标签 -->
      <div style="margin-top: 8px;">
        <div style="font-size: 11px; color: #909399; margin-bottom: 4px;">目标类型</div>
        <el-select
          :model-value="detAnnotRef?.defaultCategoryId ?? null"
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
      <!-- v2.5.14: 移除原"清空未保存"按钮 (合并到 Section 1 重做按钮, 弹窗更友好) -->
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

    <!-- 5. 提交
         v2.5.14: 移除「取消」按钮 (功能由 Section 1 的「撤销本次修改」承担)
         · 取消按钮原本是"放弃本次修改, 回到 last saved 状态"
         · 现改为更明确的「撤销本次修改」按钮, 用户语义更清晰
         v2.5.38: 移除 disabled 条件中的 `bboxList.length === 0` 误禁用
         · 之前: 用户在「清空全部」后 bboxList.length=0, 保存按钮被禁用,
         ·       无法把"删除所有 bbox"的 dirty 状态提交到后端, 旧标注仍在 DB
         · 现在: dirty 即可点保存, saveDetectionBBoxes 会先调 clearBBoxes
         ·       再以空列表循环 0 次, 实际语义为「从数据库删除全部标注」
         v2.5.39: 彻底移除 disabled 视觉态, 按钮始终看起来可点
         · 之前: !detDirty 时按钮灰显, 用户反馈「禁止点击」语义不友好
         · 现在: 始终保持 primary 高亮, 点击时由 handleSaveClick 自行判断
         v2.5.41: 整合「保存」/「保存 (清空全部)」为单一「保存」按钮
         · 之前: 按钮文字根据 bboxList.length 在「保存 (N)」和「保存 (清空全部)」
         ·       之间动态切换, 用户需要看字面推断当前语义
         · 现在: 统一显示「保存」, 不论是新增/删除/修改/全清空, 都是一次"保存"
         ·       配合无 dirty 时静默 no-op, 操作流程更直观, 无需额外确认
         · disabled 仍由 annotatorSaving 控制 (避免重复提交), 由 handleSaveClick
         ·       兜底脏状态判断, 防止无修改时误删后端已有标注 -->
    <div class="op-section">
      <div class="op-section-title">5. 提交</div>
      <div style="display: flex; gap: 8px; margin-top: 6px;">
        <el-button
          type="primary"
          :icon="Check"
          :disabled="annotatorSaving"
          :loading="annotatorSaving"
          style="flex: 1;"
          @click="handleSaveClick"
        >保存</el-button>
      </div>
    </div>

    <!-- 6. 不合格标记 (v3.0.0) -->
    <div class="op-section">
      <div class="op-section-title">6. 不合格标记</div>
      <template v-if="image">
        <template v-if="isUnqualified">
          <div style="display: flex; align-items: center; gap: 6px; margin-top: 6px;">
            <el-tag type="danger" size="small" effect="dark">已标记不合格</el-tag>
            <el-tag type="danger" size="small" effect="plain">
              {{ getRejectReasonLabel(rejectReason) }}
            </el-tag>
          </div>
          <el-button
            size="small" type="warning" :icon="RefreshLeft"
            style="width: 100%; margin-top: 6px;"
            @click="emit('unmark-unqualified')"
          >撤销不合格标记</el-button>
        </template>
        <template v-else>
          <el-select
            v-model="localRejectReason"
            placeholder="选择不合格原因"
            size="small" style="width: 100%; margin-top: 6px;"
          >
            <el-option
              v-for="opt in REJECT_REASON_OPTIONS" :key="opt.value"
              :label="opt.label" :value="opt.value"
            />
          </el-select>
          <el-input
            v-if="localRejectReason === 'other'"
            v-model="localCustomText"
            placeholder="请输入具体原因"
            size="small" style="width: 100%; margin-top: 6px;"
          />
          <el-button
            size="small" type="danger" :icon="Warning"
            :disabled="!localRejectReason"
            style="width: 100%; margin-top: 6px;"
            @click="handleMarkUnqualified"
          >标记为不合格</el-button>
        </template>
      </template>
    </div>

    <div class="op-section">
      <el-link type="primary" :icon="View" @click="emit('view-dataset')">
        去数据集详情浏览全部图片
      </el-link>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Check, Close, ArrowLeft, View, MagicStick, RefreshLeft, RefreshRight, Warning } from '@element-plus/icons-vue'
import { REJECT_REASON_OPTIONS, getRejectReasonLabel } from '@/utils/rejectReason'

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
  // v3.0.0: 不合格标记状态 (从父组件 image 派生)
  isUnqualified: boolean
  rejectReason: string | null
}>()

const emit = defineEmits<{
  // v2.5.14: 移除 undo, redo, clear-draft, cancel 事件
  // · 撤销/重做/清空 改为直接调用 detAnnotRef 子组件方法 (无需经过父组件)
  // · 取消功能被「撤销本次修改」按钮替代
  (e: 'save'): void
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
  // v3.0.0: 不合格标记事件 (单向数据流, 由父组件处理 API 调用)
  (e: 'mark-unqualified', reason: string, customText: string): void
  (e: 'unmark-unqualified'): void
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

/**
 * v2.5.39: 保存按钮的智能点击包装
 * - 取代原先 `:disabled="!detDirty || annotatorSaving"` 的双重禁用
 * - 目的: 让保存按钮始终保持 primary 高亮态 (用户不再看到「禁止点击」的灰显),
 *         但通过点击时的 dirty 判断避免无修改时点保存会误删后端 bbox
 * - 行为:
 *   1) annotatorSaving=true: 由 :disabled 控制, 此函数不会被触发
 *   2) detDirty=true: 正常 emit('save'), 走 saveDetectionBBoxes 流程
 *      (含 bboxList=0 时的「清空全部」语义 — 内部会先 clearBBoxes 再写 0 条)
 *   3) detDirty=false: 静默 no-op, 不发任何请求, 也不弹提示信息,
 *      (防止 parent 的 saveDetectionBBoxes 先调 clearBBoxes 误删后端已有标注)
 *
 * v2.5.41: 移除「当前无修改, 无需保存」info 提示
 * - 之前: 弹 ElMessage.info 告诉用户"没东西可保存", 打断操作流
 * - 现在: 静默返回, 用户在 Section 4 的「当前 bbox (N)」和 Section 1
 *   撤销按钮的 disabled 状态上, 已经能直观判断当前是否有未保存修改
 * - 这是把"保存"按钮的两种语义(普通保存/清空全部保存)统一为单一"保存"
 *   后的配套体验, 避免空点保存时被消息框分散注意力
 *
 * v2.5.42: 修复「清空全部」后点保存按钮无响应问题
 * - 根因: 之前 detDirty 的更新依赖子组件 watch(dirty) → emit('dirty-change') → 父 onDetDirtyChange
 *   链条, 任何环节时序问题或 ref 同步异常都会导致 detDirty 仍是 false,
 *   此时 handleSaveClick 走 if (!props.detDirty) return 静默分支, 用户看到「点保存无反应」
 * - 修复: 移除 handleSaveClick 的 dirty 短路, 统一 emit('save') 由父组件处理;
 *   父组件的 @save 直接调 saveDetectionBBoxes(bboxList), 完全跳过「dirty→save」的链式判定
 * - 兜底: saveDetectionBBoxes 内部若检测到 bboxes 与后端无差异, 跳过网络请求
 *   (防止用户在「未做任何修改」状态点保存时, 误删后端已保存的 bbox)
 * - 用户体验: 无论 detDirty 状态如何, 点击「保存」按钮都会立即触发保存流程;
 *   撤销按钮的 disabled 仍然基于 detDirty, 作为「当前是否有未保存改动」的视觉提示
 */
function handleSaveClick() {
  emit('save')
}

// ============== v3.0.0: 不合格标记本地状态 ==============
const localRejectReason = ref<string>('')
const localCustomText = ref<string>('')

function handleMarkUnqualified() {
  if (!localRejectReason.value) return
  emit('mark-unqualified', localRejectReason.value, localCustomText.value)
  localRejectReason.value = ''
  localCustomText.value = ''
}

// 切换图片时清空本地状态 (避免上一张的选择残留)
watch(() => props.image?.id, () => {
  localRejectReason.value = ''
  localCustomText.value = ''
})
</script>

<style scoped>
/* 跟随父 el-col 高度, 与左侧侧栏/中间画布三列同高
   - el-card 本体 100% 填充 el-col
   - body 内部 flex 1 + auto overflow, 内容过长时本卡片内部滚动 */
.op-card {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.op-card :deep(.el-card__body) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

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

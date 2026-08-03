<script setup lang="ts">
/**
 * DatasetDetail - 数据集详情页 (v3.x 嵌套容器布局优化)
 *
 * 拆分架构 (子组件 / composables):
 * - composables/useDatasetDetail     (核心数据加载/分页/筛选/导出/导航)
 * - composables/useImageSelection    (选中态管理/全选)
 * - composables/useImageBatchOps     (单图/批量删除 + 清除标注)
 * - composables/useAiLabeling        (fine-tune 模型加载 + AI 预标注)
 * - composables/useConfidencePreview (测评状态 + 启动 + tooltip 文案)
 * - composables/useImageView         (网格/列表视图 + 详情弹窗 + 上传弹窗 + 滚动位置保持)
 *
 * 业务组件 (本页面私有):
 * - components/DatasetHero           顶部 hero
 * - components/DatasetStatsRow       5 张统计卡
 * - components/DatasetFilterBar      过滤 + AI 选项 + 操作工具条
 * - components/DatasetImageGrid      图像网格视图 (多列卡片)
 * - components/DatasetImageList      图像列表视图 (单列卡片列表)
 * - components/AnnotationViewer      (外部: 详情查看)
 * - components/PreviewDialog         (外部: 测评结果)
 * - components/UploadQueue           (外部: 复用自 Datasets 页)
 *
 * v3.x 布局优化:
 * - 嵌套容器 .ds-content: 筛选栏 / 独立滚动图片区 / 分页栏
 * - 图片区 .ds-image-area 内部独立 overflow-y: auto, 整页不再滚动
 * - 视图切换时通过 imageAreaRef 保存/恢复 scrollTop, 浏览位置不丢
 * - 挂载/卸载时切换 .app-main--locked, 抑制父级滚动
 *
 * Page 仅保留:
 * - 编排各 composables (串联数据/操作)
 * - 弹窗可见性 (viewerOpen / uploadOpen / previewDialogVisible)
 * - view 编排 (模板 + 事件转发)
 */
import { ElMessage } from 'element-plus'
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { annotationApi } from '@/api'
import { REJECT_REASON_OPTIONS } from '@/utils/rejectReason'
import { useDatasetDetail } from '@/composables/useDatasetDetail'
import { useImageSelection } from '@/composables/useImageSelection'
import { useImageBatchOps } from '@/composables/useImageBatchOps'
import { useAiLabeling } from '@/composables/useAiLabeling'
import { useConfidencePreview } from '@/composables/useConfidencePreview'
import { useImageView } from '@/composables/useImageView'

// 外部组件 (与 Datasets 页共享)
import AnnotationViewer from './components/AnnotationViewer.vue'
import UploadQueue from '../Datasets/components/UploadQueue.vue'
import PreviewDialog from './components/PreviewDialog.vue'

// 本页私有组件
import DatasetHero from './components/DatasetHero.vue'
import DatasetStatsRow from './components/DatasetStatsRow.vue'
import DatasetFilterBar from './components/DatasetFilterBar.vue'
import DatasetImageGrid from './components/DatasetImageGrid.vue'
import DatasetImageList from './components/DatasetImageList.vue'

// ============== 数据加载 / 状态 ==============
const {
  dataset, images, total, page, pageSize, pageSizes,
  statusFilter, keyword, loading, stats, categories, activeModel,
  datasetIdRef,
  statusOptions,
  filteredImages, pendingCount, currentDatasetTaskType,
  load, changePage, onSizeChange,
  goBack, goAnnotate: _goAnnotate,
  handleExport,
  formatBytes, confColor, statusType, statusLabel, hasAnnotation,
} = useDatasetDetail({ autoLoad: true })

// ============== 图像选中 ==============
const {
  selectedIds, allOnPageSelected,
  toggleSelect, toggleSelectAll,
  removeId,
} = useImageSelection({ visibleImages: filteredImages })

// ============== 批量/单图操作 ==============
const {
  deleteOne, clearOneAnnotation,
  batchDelete, batchClearAnnotation,
} = useImageBatchOps({
  selectedIds,
  removeId,
  reload: load,
  currentTaskType: currentDatasetTaskType,
})

// ============== AI 标注 ==============
const {
  finetuneModels, selectedFinetuneId, threshold, autoLabeling,
  hasFinetuneModel, displayModel,
  loadFinetuneModels, onAutoAnnotate,
} = useAiLabeling({ datasetId: datasetIdRef })

// 在初次 load 完成后, 串行加载 fine-tune 模型
;(async () => {
  await loadFinetuneModels()
})()

// ============== 测评 ==============
const {
  previewing, previewDialogVisible, previewResult,
  previewTooltipText, onPreviewConfidence,
} = useConfidencePreview({
  datasetId: datasetIdRef,
  images,
  currentTaskType: currentDatasetTaskType,
  hasFinetuneModel,
  selectedFinetuneId,
  threshold,
})

// ============== 视图 / 弹窗 ==============
const {
  viewMode, gridSize, viewerOpen, viewerImageId, uploadOpen,
  openViewer, closeViewer, openUpload, setViewMode,
} = useImageView()

// ============== 图片展示区 DOM ref (用于视图切换时保持滚动位置) ==============
const imageAreaRef = ref<HTMLElement | null>(null)

/**
 * 视图切换: 包装 setViewMode, 传入图片展示区 DOM
 * - 切换前保存当前 scrollTop
 * - 切换后 nextTick 恢复 (避免浏览位置丢失)
 */
function onViewModeChange(mode: 'grid' | 'list') {
  return setViewMode(mode, imageAreaRef.value)
}

/**
 * 网格尺寸变更: 切换时保存当前滚动位置, 切换后恢复
 * - 不同 gridSize 改变每行卡片数, DOM 高度会变化, 需要保持浏览位置
 */
async function onGridSizeChange(size: 'small' | 'medium' | 'large') {
  if (size === gridSize.value) return
  const area = imageAreaRef.value
  const savedTop = area ? area.scrollTop : 0
  gridSize.value = size
  await nextTick()
  if (area) area.scrollTop = savedTop
}

// ============== 导航 (透传 selectedIds) ==============
function goAnnotate() { _goAnnotate(selectedIds) }

// ============== 测评 → 一键应用 (弹 apply 按钮) ==============
async function onPreviewApply() {
  previewDialogVisible.value = false
  await onAutoAnnotate()
  await load()
}

// ============== 列表行复选框切换 (grid 模式) ==============
function onGridToggleCheckbox(id: number, checked: boolean) {
  if (checked) {
    if (!selectedIds.value.includes(id)) {
      selectedIds.value = [...selectedIds.value, id]
    }
  } else {
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
  }
}

// ============== 上传完成回调 ==============
function onUploaded(r: any) {
  ElMessage.success(`上传 ${r?.uploaded ?? 0}/${r?.total ?? 0} 张成功`)
}

// ============== v3.0.0: 不合格图片标记 ==============

/** 批量标记弹窗 state */
const batchMarkDialogVisible = ref(false)
const batchRejectReason = ref<string>('')
const batchCustomText = ref<string>('')

/** 单图撤销不合格标记 (网格视图 hover 撤销按钮) */
async function onUnmarkUnqualified(img: any) {
  if (!img?.id) return
  try {
    await annotationApi.unmarkUnqualified(img.id)
    ElMessage.success('已撤销不合格标记')
    // v3.0.0: 撤销后必须刷新 stats (不合格数 -1) + 列表
    // - 若当前筛选为 unqualified, 撤销后该图应从列表消失
    // - 若为其他筛选, 重拉保证 stats 实时更新 (不合格指标卡数字同步变化)
    await load()
  } catch (e: any) {
    ElMessage.error('撤销失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

/** 点击「不合格」统计卡 → 切换到不合格筛选 (v3.0.0) */
function onFilterUnqualified() {
  if (statusFilter.value === 'unqualified') return
  statusFilter.value = 'unqualified'
}

/** 打开批量标记弹窗 */
function openBatchMarkDialog() {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先选中要标记的图片')
    return
  }
  batchRejectReason.value = ''
  batchCustomText.value = ''
  batchMarkDialogVisible.value = true
}

/** 确认批量标记 */
async function confirmBatchMarkUnqualified() {
  if (!batchRejectReason.value) {
    ElMessage.warning('请选择不合格原因')
    return
  }
  try {
    await annotationApi.batchMarkUnqualified({
      image_ids: selectedIds.value,
      reason: batchRejectReason.value,
      custom_text: batchCustomText.value || undefined,
    })
    ElMessage.success(`已标记 ${selectedIds.value.length} 张图片为不合格`)
    batchMarkDialogVisible.value = false
    // 清空选中 + 重新加载, 让不合格红色覆盖层显示出来
    selectedIds.value = []
    await load()
  } catch (e: any) {
    ElMessage.error('批量标记失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// ============== 整页滚动隔离 ==============
/**
 * v3.x 布局优化: 图片区需要独立滚动, 整页不能跟随滚动
 * - 父级 .app-main 默认 overflow: auto, 内容溢出时会整页滚动
 * - 这里挂载/卸载时切换 .app-main 的 overflow, 仅在本页生效
 * - 用 class 而不是直接改 style: 避免污染其他可能直接读取的样式
 */
onMounted(() => {
  const main = document.querySelector('.app-main') as HTMLElement | null
  if (main) main.classList.add('app-main--locked')
})
onBeforeUnmount(() => {
  const main = document.querySelector('.app-main') as HTMLElement | null
  if (main) main.classList.remove('app-main--locked')
})
</script>

<template>
  <div v-loading="loading" class="ds-page">
    <!-- 顶部 hero -->
    <DatasetHero
      :dataset="dataset"
      @back="goBack"
      @refresh="load"
      @upload="openUpload"
      @export="(fmt: 'coco' | 'yolo' | 'csv') => handleExport(fmt)"
    />

    <!-- 5 张统计卡 (v3.0.0: 含不合格指标, 同一行 flex 等分) -->
    <DatasetStatsRow
      :dataset="dataset"
      :stats="stats"
      :categories="categories"
      @click-unqualified="onFilterUnqualified"
    />

    <!-- 嵌套容器: 筛选栏 / 独立滚动图片区 / 分页栏 -->
    <div class="ds-content">
      <!-- 过滤 + AI 选项 + 操作工具条 -->
      <DatasetFilterBar
        :status-options="statusOptions"
        v-model:status-filter="statusFilter"
        v-model:keyword="keyword"
        :finetune-models="finetuneModels"
        v-model:selected-finetune-id="selectedFinetuneId"
        :active-model="activeModel"
        v-model:threshold="threshold"
        :has-finetune-model="hasFinetuneModel"
        :display-model="displayModel"
        :pending-count="pendingCount"
        :preview-tooltip-text="previewTooltipText"
        :previewing="previewing"
        :has-images="images.length > 0"
        :selected-count="selectedIds.length"
        :all-on-page-selected="allOnPageSelected"
        :view-mode="viewMode"
        :grid-size="gridSize"
        :task-type="currentDatasetTaskType"
        @update:view-mode="onViewModeChange"
        @update:grid-size="onGridSizeChange"
        @preview="onPreviewConfidence"
        @go-annotate="goAnnotate"
        @batch-clear="batchClearAnnotation"
        @batch-delete="batchDelete"
        @toggle-select-all="toggleSelectAll"
        @batch-mark-unqualified="openBatchMarkDialog"
      />

      <!-- 图片展示区: 独立滚动容器, 仅本区域响应滚轮 -->
      <div ref="imageAreaRef" class="ds-image-area">
        <el-empty
          v-if="!loading && filteredImages.length === 0"
          description="该状态下没有图片"
        />

        <DatasetImageGrid
          v-else-if="viewMode === 'grid'"
          :images="filteredImages"
          :selected-ids="selectedIds"
          :status-type="statusType"
          :status-label="statusLabel"
          :conf-color="confColor"
          :format-bytes="formatBytes"
          :has-annotation="hasAnnotation"
          :grid-size="gridSize"
          @toggle-select="toggleSelect"
          @toggle-checkbox="onGridToggleCheckbox"
          @open-viewer="openViewer"
          @clear-annotation="clearOneAnnotation"
          @delete-one="deleteOne"
          @unmark-unqualified="onUnmarkUnqualified"
        />

        <DatasetImageList
          v-else
          :images="filteredImages"
          :selected-ids="selectedIds"
          :status-type="statusType"
          :status-label="statusLabel"
          :conf-color="confColor"
          :format-bytes="formatBytes"
          :has-annotation="hasAnnotation"
          @toggle-select="toggleSelect"
          @toggle-checkbox="onGridToggleCheckbox"
          @open-viewer="openViewer"
          @clear-annotation="clearOneAnnotation"
          @delete-one="deleteOne"
          @unmark-unqualified="onUnmarkUnqualified"
        />
      </div>

      <!-- 分页: 始终可见, 固定在容器底部 -->
      <div class="ds-pager">
        <el-pagination
          background
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="pageSizes"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="changePage"
          @size-change="onSizeChange"
        />
      </div>
    </div>

    <!-- 标注查看器弹窗 -->
    <el-dialog
      :model-value="viewerOpen"
      title="图片详情"
      width="1080px"
      top="5vh"
      destroy-on-close
      :close-on-click-modal="false"
      @update:model-value="(v: boolean) => { if (!v) closeViewer() }"
    >
      <AnnotationViewer
        v-if="viewerImageId"
        :image-id="viewerImageId"
        @saved="load"
      />
    </el-dialog>

    <!-- 上传图片弹窗 -->
    <el-dialog
      :model-value="uploadOpen"
      :title="`上传图片到「${dataset?.name || ''}」`"
      width="780px"
      :close-on-click-modal="false"
      destroy-on-close
      @update:model-value="(v: boolean) => { if (!v) uploadOpen = false }"
      @close="load"
    >
      <UploadQueue
        v-if="uploadOpen"
        :dataset-id="datasetIdRef"
        @uploaded="onUploaded"
      />
    </el-dialog>

    <!-- 置信度测评结果弹窗 (非破坏性预览) -->
    <PreviewDialog
      v-model="previewDialogVisible"
      :result="previewResult"
      :task-type="currentDatasetTaskType"
      :auto-labeling="autoLabeling"
      @apply="onPreviewApply"
    />

    <!-- v3.0.0: 批量标记不合格弹窗 -->
    <el-dialog
      v-model="batchMarkDialogVisible"
      title="批量标记不合格"
      width="420px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form label-width="100px">
        <el-form-item label="选中数量">
          <el-tag type="warning">{{ selectedIds.length }} 张</el-tag>
        </el-form-item>
        <el-form-item label="不合格原因">
          <el-select
            v-model="batchRejectReason"
            placeholder="请选择不合格原因"
            style="width: 100%;"
          >
            <el-option
              v-for="opt in REJECT_REASON_OPTIONS"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="batchRejectReason === 'other'" label="具体原因">
          <el-input
            v-model="batchCustomText"
            placeholder="请输入具体原因"
            maxlength="64"
            show-word-limit
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="batchMarkDialogVisible = false">取消</el-button>
        <el-button type="danger" @click="confirmBatchMarkUnqualified">
          确认标记
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ====== 页面级: 占据整页高度, flex 列布局 ======
 * 嵌套结构: hero / stats / content(filter + image + pager)
 * - 关键: 三个直接子节点都是 flex-shrink: 0 的固定块, 只有 .ds-content 可伸缩
 * - 这样无论 hero/stats 高度如何变化, 都不会挤压或被父级裁剪 */
.ds-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 16px;
  gap: 12px;
  min-height: 0;
  overflow: hidden;
}

/* ====== 嵌套容器: 筛选栏 / 滚动图片区 / 分页栏 三段式 ======
 * - 视觉上是一张白卡, 内部水平分割线划分功能区
 * - 高度自适应, flex: 1 抢占 hero + stats 之外的全部空间
 *
 * v3.x 关键修正: 用 overflow: clip 而非 overflow: hidden
 * - overflow: hidden 会创建 scroll container, 成为 .image-list__header
 *   position: sticky 的「最近滚动祖先」, 截胡了真正的滚动容器 .ds-image-area
 *   结果: 表头永远在容器内相对定位, 不随图片区滚动而吸顶
 * - overflow: clip 同样裁剪到圆角, 但不创建 scroll container,
 *   sticky 元素可以正确找到 .ds-image-area 作为滚动祖先
 *   浏览器支持: Chrome 90+ / Firefox 81+ / Safari 16+ */
.ds-content {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  overflow: clip;
}

/* 让 DatasetFilterBar 内部的 el-card 融入容器 (无外框 + 底部分割线) */
.ds-content :deep(.filter-card) {
  border: none;
  box-shadow: none;
  border-radius: 0;
  margin-bottom: 0;
  background: transparent;
  flex-shrink: 0;
}
.ds-content :deep(.filter-card .el-card__body) {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-soft);
}

/* ====== 图片展示区: 独立滚动容器 ======
 * - flex: 1 + min-height: 0: 在 flex 列布局中允许收缩到内容以下, 触发 overflow
 * - overflow-y: auto: 仅本区域响应垂直滚轮, 整页不滚
 * - padding 内置, 滚动条与卡片间距合理
 * - overflow-x: hidden: 列表视图 (列固定宽度) 在窄屏下不出现横向滚动条,
 *   由响应式 CSS 隐藏次要列来保证单行展示 */
.ds-image-area {
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 12px 16px;
  /* 自定义滚动条: 与全局浅色滚动条风格保持一致 */
  scrollbar-gutter: stable;
}

/* ====== 分页栏: 固定在容器底部, 不随图片区滚动 ======
 * - flex-shrink: 0: 即使内容溢出也不被压缩
 * - border-top 视觉分割, 呼应筛选栏的 border-bottom */
.ds-pager {
  flex-shrink: 0;
  padding: 12px 16px;
  display: flex;
  justify-content: flex-end;
  background: var(--bg-card);
  border-top: 1px solid var(--border-soft);
}

/* ====== 响应式: 窄屏隐藏统计卡元数据, 给图片区更多空间 ====== */
@media (max-width: 768px) {
  .ds-page {
    padding: 12px;
    gap: 8px;
  }
  .ds-image-area {
    padding: 8px 12px;
  }
  .ds-pager {
    padding: 8px 12px;
  }
}
</style>

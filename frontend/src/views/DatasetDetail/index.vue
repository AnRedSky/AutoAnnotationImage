<script setup lang="ts">
/**
 * DatasetDetail - 数据集详情页 (v3.0.0 Phase L 重构为编排层)
 *
 * 拆分架构 (子组件 / composables):
 * - composables/useDatasetDetail     (核心数据加载/分页/筛选/导出/导航)
 * - composables/useImageSelection    (选中态管理/全选)
 * - composables/useImageBatchOps     (单图/批量删除 + 清除标注)
 * - composables/useAiLabeling        (fine-tune 模型加载 + AI 预标注)
 * - composables/useConfidencePreview (测评状态 + 启动 + tooltip 文案)
 * - composables/useImageView         (网格/列表视图 + 详情弹窗 + 上传弹窗)
 *
 * 业务组件 (本页面私有):
 * - components/DatasetHero           顶部 hero
 * - components/DatasetStatsRow       4 张统计卡
 * - components/DatasetFilterBar      过滤 + AI 选项 + 操作工具条
 * - components/DatasetImageGrid      图像网格视图
 * - components/DatasetImageList      图像列表视图
 * - components/AnnotationViewer      (外部: 详情查看)
 * - components/PreviewDialog         (外部: 测评结果)
 * - components/UploadQueue           (外部: 复用自 Datasets 页)
 *
 * Page 仅保留:
 * - 编排各 composables (串联数据/操作)
 * - 弹窗可见性 (viewerOpen / uploadOpen / previewDialogVisible)
 * - view 编排 (模板 + 事件转发)
 */
import { ElMessage } from 'element-plus'
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
  viewMode, viewerOpen, viewerImageId, uploadOpen,
  openViewer, closeViewer, openUpload,
} = useImageView()

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
</script>

<template>
  <div v-loading="loading" class="page-container">
    <!-- 顶部 hero -->
    <DatasetHero
      :dataset="dataset"
      @back="goBack"
      @refresh="load"
      @upload="openUpload"
      @export="(fmt: 'coco' | 'yolo' | 'csv') => handleExport(fmt)"
    />

    <!-- 4 张统计卡 -->
    <DatasetStatsRow
      :dataset="dataset"
      :stats="stats"
      :categories="categories"
    />

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
      v-model:view-mode="viewMode"
      :task-type="currentDatasetTaskType"
      @preview="onPreviewConfidence"
      @go-annotate="goAnnotate"
      @batch-clear="batchClearAnnotation"
      @batch-delete="batchDelete"
      @toggle-select-all="toggleSelectAll"
    />

    <!-- 图像网格 / 列表 -->
    <el-empty v-if="!loading && filteredImages.length === 0" description="该状态下没有图片" />

    <DatasetImageGrid
      v-else-if="viewMode === 'grid'"
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
    />

    <DatasetImageList
      v-else
      :images="filteredImages"
      :status-type="statusType"
      :status-label="statusLabel"
      :conf-color="confColor"
      :format-bytes="formatBytes"
      :has-annotation="hasAnnotation"
      @row-click="toggleSelect"
      @open-viewer="openViewer"
      @clear-annotation="clearOneAnnotation"
      @delete-one="deleteOne"
    />

    <!-- 分页: 始终可见 -->
    <div class="pager">
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
  </div>
</template>

<style scoped>
.page-container {
  padding: 16px;
}

/* 分页: 始终可见 */
.pager {
  margin-top: 12px;
  padding: 8px 0;
  display: flex;
  justify-content: flex-end;
  background: #fff;
  border-top: 1px solid var(--border-soft);
  position: sticky;
  bottom: 0;
  z-index: 5;
}
</style>

<!--
  SegmentationPanel.vue (v2.5.7 拆分自 Annotate.vue)
  ===================================================
  图像分割任务右侧操作面板 (6 sections)

  设计:
  - 6 sections: 1 工具模式 / 2 类别 / 3 笔刷大小 / 4 (空, 缩放冗余已删) / 5 图片导航 / 6 mask 状态 / 7 提交
  - 接收 segAnnotRef (模板 ref, 用 ?.value?.xx 访问内部状态)
  - 通过 emit 抛出操作

  Props (业务通用, 弱耦合):
    segAnnotRef:        ref<any> 子组件模板 ref
    segDirty:           dirty 状态
    annotatorSaving:    保存中 loading
    categories:         类别列表
    segMode:            当前模式 (brush/erase/pan)
    canGoPrev, noMore, historyCursor, historyIds, image

  Emits:
    mode-change, category-change, brush-size-change
    prev, next, view-dataset
    save, cancel
-->
<template>
  <el-card title="分割操作面板">
    <!-- 1. 工具模式 -->
    <div class="op-section">
      <div class="op-section-title">1. 工具模式</div>
      <el-radio-group :model-value="segMode" size="small" @change="(v: any) => emit('mode-change', v)">
        <el-radio-button value="brush">画刷 (B)</el-radio-button>
        <el-radio-button value="erase">橡皮 (E)</el-radio-button>
        <el-radio-button value="pan">查看 (V)</el-radio-button>
      </el-radio-group>
      <div style="margin-top: 4px; font-size: 11px; color: #909399;">
        <template v-if="segMode === 'brush'">按住鼠标画当前类别, 释放停止</template>
        <template v-else-if="segMode === 'erase'">按住鼠标擦除像素, 释放停止</template>
        <template v-else>按住鼠标拖动查看画布</template>
      </div>
    </div>

    <!-- 2. 当前画刷类别 -->
    <div class="op-section" v-if="categories.length">
      <div class="op-section-title">2. 当前画刷类别</div>
      <el-select
        :model-value="segAnnotRef?.brushCategoryId?.value ?? null"
        placeholder="选择类别"
        size="small"
        style="width: 100%; margin-top: 6px;"
        filterable
        @change="(v: number) => emit('category-change', v)"
      >
        <el-option
          v-for="c in categories" :key="c.id"
          :label="c.name" :value="c.id"
        >
          <span class="cat-dot" :style="{ background: catColor(c.id) }"></span>
          {{ c.name }}
        </el-option>
      </el-select>
    </div>

    <!-- 3. 当前画刷大小 -->
    <div class="op-section">
      <div class="op-section-title">3. 笔刷大小: {{ segAnnotRef?.brushSize?.value ?? 12 }}px</div>
      <el-slider
        :model-value="segAnnotRef?.brushSize?.value ?? 12"
        :min="2" :max="40" :step="1"
        style="margin-top: 6px;"
        @input="(v: number) => emit('brush-size-change', v)"
      />
    </div>

    <!-- 5. 图片导航 -->
    <div class="op-section">
      <div class="op-section-title">5. 图片导航</div>
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

    <!-- 6. mask 状态 -->
    <div class="op-section">
      <div class="op-section-title">6. 当前 mask 状态</div>
      <div style="font-size: 12px; color: #606266; margin-top: 6px;">
        <div>画布尺寸: {{ image?.width ?? '?' }} × {{ image?.height ?? '?' }} px</div>
        <div v-if="segAnnotRef?.maskStats?.value">
          已标像素:
          <span style="color: #67c23a; font-weight: 600;">
            {{ segAnnotRef.maskStats.value.painted }} / {{ segAnnotRef.maskStats.value.total }}
          </span>
          ({{ (segAnnotRef.maskStats.value.painted / segAnnotRef.maskStats.value.total * 100).toFixed(1) }}%)
        </div>
        <div v-else>已标像素: <span style="color: #909399;">—</span></div>
        <div>坐标 (鼠标): x={{ segAnnotRef?.mousePos?.value?.x ?? '—' }}, y={{ segAnnotRef?.mousePos?.value?.y ?? '—' }} px</div>
      </div>
    </div>

    <!-- 7. 提交 -->
    <div class="op-section">
      <div class="op-section-title">7. 提交</div>
      <div style="display: flex; gap: 8px; margin-top: 6px;">
        <el-button
          type="primary"
          :icon="Check"
          :disabled="!segDirty || annotatorSaving"
          :loading="annotatorSaving"
          style="flex: 1;"
          @click="emit('save')"
        >保存 mask</el-button>
        <el-button
          :icon="Close" style="flex: 1;"
          :disabled="!segDirty || annotatorSaving"
          @click="emit('cancel')"
        >取消</el-button>
      </div>
      <div v-if="segDirty" style="margin-top: 4px; font-size: 11px; color: #e6a23c;">
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
import { Check, Close, ArrowLeft, View } from '@element-plus/icons-vue'

interface Category { id: number; name: string }
interface Image {
  id: number
  filename: string
  width: number
  height: number
}

defineProps<{
  segAnnotRef: any
  segDirty: boolean
  annotatorSaving: boolean
  categories: Category[]
  segMode: 'brush' | 'erase' | 'pan'
  canGoPrev: boolean
  noMore: boolean
  historyCursor: number
  historyIds: number[]
  image: Image | null
}>()

const emit = defineEmits<{
  (e: 'mode-change', m: 'brush' | 'erase' | 'pan'): void
  (e: 'category-change', catId: number): void
  (e: 'brush-size-change', size: number): void
  (e: 'prev'): void
  (e: 'next'): void
  (e: 'view-dataset'): void
  (e: 'save'): void
  (e: 'cancel'): void
}>()

// 调色板 (与 SegmentationAnnotator 一致)
const DET_PALETTE = [
  '#f56c6c', '#67c23a', '#409eff', '#e6a23c',
  '#909399', '#9b59b6', '#1abc9c', '#ff5722',
]
function catColor(catId: number | null | undefined): string {
  if (catId == null) return '#909399'
  return DET_PALETTE[Math.abs(Number(catId)) % DET_PALETTE.length]
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
  background: #67c23a;
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
</style>

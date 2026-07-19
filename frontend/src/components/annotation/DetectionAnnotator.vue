<!--
  DetectionAnnotator.vue
  =====================
  目标检测 bbox 画布组件 (v2.1.0)

  职责:
  - 加载原图到 canvas (含 HiDPI 自适应)
  - 鼠标拖拽绘制新 bbox (mousedown -> mousemove -> mouseup)
  - 渲染已有 bbox 列表 (颜色按 category_id 分配)
  - 支持选中 / 删除 / 改类别 bbox
  - 通过 emit('change', bboxes) 抛出当前 bbox 列表, 由父组件 (Annotate.vue) 负责持久化

  设计原则:
  - 零业务耦合: 不直接调 API, 不引 store; 数据全靠 props 传入, 操作全靠 emit
  - 坐标存储: 归一化 (0-1), 与后端 BBoxAnnotation.x_min/y_min/x_max/y_max 一致
  - 渲染坐标系: 实际像素 (canvas size), 由组件内部转换

  Props:
    imageUrl:    原图 URL (必填)
    imageId:     当前图 id (用于 emit)
    imageWidth:  原图实际宽
    imageHeight: 原图实际高
    categories:  [{id, name, color?}]  类别列表 (用于下拉选 + 颜色)
    modelValue:  当前 bbox 列表 [{x_min, y_min, x_max, y_max, category_id, id?}]

  Emits:
    update:modelValue  bbox 列表变更
    save               触发父组件保存 (父组件拿当前 modelValue 调后端 API)
    cancel             撤销未保存的变更 (父组件可重读 server-side bbox)
-->
<template>
  <div class="det-annotator">
    <!-- 工具栏 -->
    <div class="toolbar">
      <el-button-group size="small">
        <el-button :type="mode === 'draw' ? 'primary' : 'default'" @click="mode = 'draw'">
          <el-icon><EditPen /></el-icon>绘制
        </el-button>
        <el-button :type="mode === 'edit' ? 'primary' : 'default'" @click="mode = 'edit'">
          <el-icon><Select /></el-icon>编辑
        </el-button>
        <el-button @click="clearDraft">清空未保存</el-button>
      </el-button-group>
      <span class="hint">
        {{ mode === 'draw'
            ? '在图片上按住鼠标左键拖拽, 释放后完成一个 bbox'
            : '点击已有 bbox 可选中删除 / 改类别' }}
      </span>
    </div>

    <!-- 画布区域 -->
    <div ref="wrapRef" class="canvas-wrap">
      <canvas
        ref="canvasRef"
        class="canvas"
        :width="canvasSize.w"
        :height="canvasSize.h"
        @mousedown="onMouseDown"
        @mousemove="onMouseMove"
        @mouseup="onMouseUp"
        @mouseleave="onMouseUp"
      />
    </div>

    <!-- 类别下拉 (绘制模式时设置下一个 bbox 的默认类别) -->
    <div class="cat-bar" v-if="mode === 'draw'">
      <span>新 bbox 类别:</span>
      <el-select v-model="defaultCategoryId" placeholder="选择类别" size="small" style="width: 200px;" filterable>
        <el-option
          v-for="c in categories" :key="c.id" :value="c.id"
          :label="c.name"
        >
          <span class="cat-dot" :style="{ background: colorOf(c.id) }"></span>
          {{ c.name }}
        </el-option>
      </el-select>
    </div>

    <!-- bbox 列表 (用于在编辑模式选择 / 改类别 / 删除) -->
    <div class="bbox-list" v-if="modelValue && modelValue.length > 0">
      <div class="list-title">当前 bbox ({{ modelValue.length }})</div>
      <el-tag
        v-for="(b, idx) in modelValue" :key="b.id || idx"
        :type="selectedIndex === idx ? 'primary' : 'info'"
        :effect="selectedIndex === idx ? 'dark' : 'plain'"
        class="bbox-tag"
        @click="selectIndex(idx)"
        closable
        @close="removeAt(idx)"
      >
        <span class="cat-dot" :style="{ background: colorOf(b.category_id) }"></span>
        #{{ idx + 1 }} {{ catName(b.category_id) }}
      </el-tag>
    </div>

    <!-- 操作按钮 -->
    <div class="actions">
      <el-button type="primary" :icon="Check" :disabled="!dirty" @click="onSave">
        保存 ({{ modelValue?.length || 0 }})
      </el-button>
      <el-button @click="$emit('cancel')">取消</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, EditPen, Select } from '@element-plus/icons-vue'

// ============== Props / Emits ==============
interface BBox {
  id?: number
  x_min: number
  y_min: number
  x_max: number
  y_max: number
  category_id: number
  confidence?: number
}
interface Category {
  id: number
  name: string
  color?: string
}
const props = defineProps<{
  imageUrl: string
  imageId: number
  imageWidth: number
  imageHeight: number
  categories: Category[]
  modelValue: BBox[]
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: BBox[]): void
  (e: 'save', bboxes: BBox[]): void
  (e: 'cancel'): void
}>()

// ============== State ==============
const mode = ref<'draw' | 'edit'>('draw')
const selectedIndex = ref<number | null>(null)
const defaultCategoryId = ref<number | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
const wrapRef = ref<HTMLDivElement | null>(null)
const imgEl = new Image()
const canvasSize = ref<{ w: number; h: number }>({ w: 0, h: 0 })

// 绘制中临时状态
const drawing = ref<{ x0: number; y0: number; x1: number; y1: number } | null>(null)

// dirty: modelValue 与初始化时不一致视为有未保存改动
const initial = ref<string>(JSON.stringify(props.modelValue || []))
const dirty = computed(() => JSON.stringify(props.modelValue || []) !== initial.value)

// ============== 类别调色板 (固定 8 色, 按 category_id hash) ==============
const PALETTE = [
  '#f56c6c', '#67c23a', '#409eff', '#e6a23c',
  '#909399', '#9b59b6', '#1abc9c', '#ff5722',
]
function colorOf(catId: number | null | undefined): string {
  if (catId == null) return '#909399'
  return PALETTE[Math.abs(Number(catId)) % PALETTE.length]
}
function catName(catId: number | null | undefined): string {
  const c = props.categories.find((x) => x.id === catId)
  return c?.name || `cls_${catId}`
}

// 监听 categories 变化, 默认选第一个
watch(
  () => props.categories,
  (cats) => {
    if (defaultCategoryId.value == null && cats.length > 0) {
      defaultCategoryId.value = cats[0].id
    }
  },
  { immediate: true }
)

// 监听 imageUrl 变化, 重新加载图片
watch(
  () => props.imageUrl,
  () => loadImage()
)
onMounted(() => loadImage())

// ============== 图片加载 + canvas 尺寸 ==============
function loadImage() {
  if (!props.imageUrl) return
  imgEl.crossOrigin = 'anonymous'
  imgEl.onload = () => {
    // 限定画布: 最大 600 宽, 等比缩放
    const maxW = 600
    const scale = Math.min(1, maxW / (imgEl.naturalWidth || props.imageWidth || 1))
    canvasSize.value = {
      w: Math.round((imgEl.naturalWidth || props.imageWidth) * scale),
      h: Math.round((imgEl.naturalHeight || props.imageHeight) * scale),
    }
    nextTick(() => draw())
  }
  imgEl.onerror = () => {
    ElMessage.error('图片加载失败')
  }
  imgEl.src = props.imageUrl
}

// ============== 鼠标事件 -> 坐标转换 ==============
function eventToImage(e: MouseEvent): { x: number; y: number } {
  if (!canvasRef.value) return { x: 0, y: 0 }
  const rect = canvasRef.value.getBoundingClientRect()
  const x = (e.clientX - rect.left) * (canvasRef.value.width / rect.width)
  const y = (e.clientY - rect.top) * (canvasRef.value.height / rect.height)
  return { x, y }
}
function pixelToNorm(p: { x: number; y: number }): { x: number; y: number } {
  return {
    x: Math.max(0, Math.min(1, p.x / canvasSize.value.w)),
    y: Math.max(0, Math.min(1, p.y / canvasSize.value.h)),
  }
}

// ============== 鼠标事件处理 ==============
function onMouseDown(e: MouseEvent) {
  if (mode.value === 'draw') {
    const p = eventToImage(e)
    drawing.value = { x0: p.x, y0: p.y, x1: p.x, y1: p.y }
  } else {
    // edit: 命中检测
    const p = pixelToNorm(eventToImage(e))
    const hitIdx = findHitIndex(p)
    selectIndex(hitIdx)
  }
}
function onMouseMove(e: MouseEvent) {
  if (!drawing.value) return
  const p = eventToImage(e)
  drawing.value.x1 = p.x
  drawing.value.y1 = p.y
  draw()
}
function onMouseUp(_e: MouseEvent) {
  if (!drawing.value) return
  const d = drawing.value
  drawing.value = null
  // 归一化 + 排序
  const xMin = Math.min(d.x0, d.x1) / canvasSize.value.w
  const yMin = Math.min(d.y0, d.y1) / canvasSize.value.h
  const xMax = Math.max(d.x0, d.x1) / canvasSize.value.w
  const yMax = Math.max(d.y0, d.y1) / canvasSize.value.h
  // 过滤太小的拖拽 (像素 < 5)
  const wPx = Math.abs(d.x1 - d.x0)
  const hPx = Math.abs(d.y1 - d.y0)
  if (wPx < 5 || hPx < 5) {
    draw(); return
  }
  // 类别必须选
  if (defaultCategoryId.value == null) {
    ElMessage.warning('请先在下方选一个类别')
    draw(); return
  }
  const next = [
    ...(props.modelValue || []),
    {
      x_min: round(xMin),
      y_min: round(yMin),
      x_max: round(xMax),
      y_max: round(yMax),
      category_id: defaultCategoryId.value,
    },
  ]
  emit('update:modelValue', next)
  draw()
}
function findHitIndex(p: { x: number; y: number }): number | null {
  const list = props.modelValue || []
  for (let i = 0; i < list.length; i++) {
    const b = list[i]
    if (p.x >= b.x_min && p.x <= b.x_max && p.y >= b.y_min && p.y <= b.y_max) {
      return i
    }
  }
  return null
}
function selectIndex(i: number | null) {
  selectedIndex.value = i
  draw()
}
function removeAt(i: number) {
  const list = [...(props.modelValue || [])]
  list.splice(i, 1)
  emit('update:modelValue', list)
  if (selectedIndex.value === i) selectedIndex.value = null
  else if (selectedIndex.value != null && selectedIndex.value > i) selectedIndex.value -= 1
  draw()
}
function clearDraft() {
  if (!props.modelValue || props.modelValue.length === 0) return
  ElMessageBoxConfirm('清空所有 bbox? (未保存)').then(() => {
    emit('update:modelValue', [])
    selectedIndex.value = null
    draw()
  }).catch(() => {})
}

// 简化: 内联 ElMessageBox.confirm (避免再 import)
function ElMessageBoxConfirm(msg: string): Promise<void> {
  return ElMessageBox.confirm(msg, '确认', {
    type: 'warning',
    confirmButtonText: '清空',
    cancelButtonText: '取消',
  }).then(() => undefined).catch(() => { throw new Error('cancel') })
}

// ============== 保存 / 取消 ==============
function onSave() {
  if (!props.modelValue || props.modelValue.length === 0) {
    ElMessage.warning('至少画一个 bbox')
    return
  }
  initial.value = JSON.stringify(props.modelValue)
  emit('save', props.modelValue)
}

// ============== 渲染 ==============
function draw() {
  const c = canvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  // 清屏
  ctx.clearRect(0, 0, c.width, c.height)
  // 原图
  if (imgEl.complete && imgEl.naturalWidth > 0) {
    ctx.drawImage(imgEl, 0, 0, c.width, c.height)
  } else {
    ctx.fillStyle = '#f5f5f5'
    ctx.fillRect(0, 0, c.width, c.height)
    ctx.fillStyle = '#999'
    ctx.font = '14px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('图片加载中…', c.width / 2, c.height / 2)
  }
  // 已有 bbox
  const list = props.modelValue || []
  list.forEach((b, i) => {
    const x = b.x_min * c.width
    const y = b.y_min * c.height
    const w = (b.x_max - b.x_min) * c.width
    const h = (b.y_max - b.y_min) * c.height
    const color = colorOf(b.category_id)
    const selected = selectedIndex.value === i
    ctx.strokeStyle = color
    ctx.lineWidth = selected ? 4 : 2
    ctx.strokeRect(x, y, w, h)
    // 标签
    ctx.fillStyle = color
    ctx.fillRect(x, y - 18, 8 + ctx.measureText(`#${i + 1} ${catName(b.category_id)}`).width, 18)
    ctx.fillStyle = '#fff'
    ctx.font = '12px sans-serif'
    ctx.textAlign = 'left'
    ctx.fillText(`#${i + 1} ${catName(b.category_id)}`, x + 4, y - 4)
  })
  // 绘制中
  if (drawing.value) {
    const d = drawing.value
    const x = Math.min(d.x0, d.x1)
    const y = Math.min(d.y0, d.y1)
    const w = Math.abs(d.x1 - d.x0)
    const h = Math.abs(d.y1 - d.y0)
    ctx.strokeStyle = '#409eff'
    ctx.lineWidth = 2
    ctx.setLineDash([5, 3])
    ctx.strokeRect(x, y, w, h)
    ctx.setLineDash([])
  }
}

function round(v: number) { return Math.round(v * 10000) / 10000 }

// modelValue 变化时重绘
watch(
  () => props.modelValue,
  () => draw(),
  { deep: true }
)

// ============== 生命周期清理 ==============
onBeforeUnmount(() => {
  imgEl.onload = null
  imgEl.onerror = null
})
</script>

<style scoped>
.det-annotator {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.toolbar .hint {
  color: #909399;
  font-size: 12px;
}
.canvas-wrap {
  position: relative;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: hidden;
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 360px;
}
.canvas {
  display: block;
  max-width: 100%;
  cursor: crosshair;
  user-select: none;
}
.cat-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.bbox-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.list-title {
  font-size: 12px;
  color: #909399;
  margin-right: 4px;
}
.bbox-tag {
  cursor: pointer;
}
.cat-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
  vertical-align: middle;
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
</style>

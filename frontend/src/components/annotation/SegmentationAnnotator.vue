<!--
  SegmentationAnnotator.vue
  =========================
  图像分割 mask 画布组件 (v2.1.0)

  职责:
  - 加载原图 + 已存在的 mask (来自后端 /api/segmentation/masks/{image_id})
  - 渲染: 原图为底, mask 用半透明彩色叠层 (调色板按 category_id 分配)
  - 画刷模式 (brush): 鼠标按住画当前类别, 释放停止
  - 橡皮模式 (erase): 鼠标按住擦除
  - 调色板: 用户在工具栏选当前画刷类别, 颜色从 PALETTE 取
  - 通过 emit('save', maskBlob) 抛出 PNG 文件, 由父组件负责上传到后端

  设计原则:
  - 零业务耦合: 不直接调 API, 数据全靠 props 传入
  - mask 内部表示: 离屏 canvas (P-mode-like), 与 PIL P-mode 兼容
  - 保存: 离屏 canvas 转 PNG File, emit 给父组件
  - 坐标: mask 像素坐标 (与 PIL 一致), 不做归一化

  Props:
    imageUrl:    原图 URL
    imageId:     当前图 id
    imageWidth:  原图宽
    imageHeight: 原图高
    categories:  [{id, name}] 类别 (用于调色板 + 颜色)
    initialMaskUrl: 已有 mask URL (后端 /api/segmentation/masks/{id}?download=true)

  Emits:
    save(maskFile: File)   用户点保存, 抛出 PNG File 给父组件上传
    cancel()               撤销
    clear()                清空当前画布
-->
<template>
  <div class="seg-annotator">
    <!-- 工具栏 -->
    <div class="toolbar">
      <el-button-group size="small">
        <el-button :type="mode === 'brush' ? 'primary' : 'default'" @click="mode = 'brush'">
          <el-icon><Brush /></el-icon>画刷
        </el-button>
        <el-button :type="mode === 'erase' ? 'primary' : 'default'" @click="mode = 'erase'">
          <el-icon><Delete /></el-icon>橡皮
        </el-button>
        <el-button :type="mode === 'pan' ? 'primary' : 'default'" @click="mode = 'pan'">
          <el-icon><View /></el-icon>查看
        </el-button>
      </el-button-group>
      <span class="brush-size">
        笔刷:
        <el-slider v-model="brushSize" :min="2" :max="40" :step="1" style="width: 120px;" />
        <span class="size-num">{{ brushSize }}px</span>
      </span>
      <el-button size="small" @click="clearMask" :icon="Refresh">清空</el-button>
    </div>

    <!-- 类别调色板 (画刷 / 橡皮 当前作用的类别) -->
    <div class="palette" v-if="mode === 'brush'">
      <span>当前类别:</span>
      <div
        v-for="c in categories" :key="c.id"
        class="palette-item"
        :class="{ active: brushCategoryId === c.id }"
        :style="{ borderColor: colorOf(c.id) }"
        @click="brushCategoryId = c.id"
      >
        <span class="cat-dot" :style="{ background: colorOf(c.id) }"></span>
        {{ c.name }}
      </div>
    </div>

    <!-- 双 canvas 叠层: 底层原图, 上层 mask 半透明 -->
    <div ref="wrapRef" class="canvas-wrap">
      <canvas ref="imgCanvasRef" class="layer" :width="canvasSize.w" :height="canvasSize.h" />
      <canvas
        ref="maskCanvasRef" class="layer interactive"
        :width="canvasSize.w" :height="canvasSize.h"
        @mousedown="onMouseDown"
        @mousemove="onMouseMove"
        @mouseup="onMouseUp"
        @mouseleave="onMouseUp"
      />
    </div>

    <!-- 操作按钮 -->
    <div class="actions">
      <el-button type="primary" :icon="Check" :disabled="!dirty" @click="onSave">
        保存 mask
      </el-button>
      <el-button @click="$emit('cancel')">取消</el-button>
    </div>

    <div class="meta" v-if="maskStats">
      mask 尺寸: {{ canvasSize.w }}×{{ canvasSize.h }} |
      已标像素: {{ maskStats.painted }} / {{ maskStats.total }}
      ({{ (maskStats.painted / maskStats.total * 100).toFixed(1) }}%)
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Brush, Delete, View, Refresh } from '@element-plus/icons-vue'

interface Category { id: number; name: string }
const props = defineProps<{
  imageUrl: string
  imageId: number
  imageWidth: number
  imageHeight: number
  categories: Category[]
  initialMaskUrl?: string | null
}>()
const emit = defineEmits<{
  (e: 'save', file: File): void
  (e: 'cancel'): void
  (e: 'clear'): void
}>()

// ============== State ==============
const mode = ref<'brush' | 'erase' | 'pan'>('brush')
const brushSize = ref(12)
const brushCategoryId = ref<number | null>(null)
const canvasSize = ref<{ w: number; h: number }>({ w: 0, h: 0 })
const imgCanvasRef = ref<HTMLCanvasElement | null>(null)
const maskCanvasRef = ref<HTMLCanvasElement | null>(null)
const wrapRef = ref<HTMLDivElement | null>(null)
const imgEl = new Image()
const maskEl = new Image()
const dirty = ref(false)
const initial = ref<string>('')

const PALETTE = [
  'rgba(245,108,108,0.55)',  // 红
  'rgba(103,194,58,0.55)',   // 绿
  'rgba(64,158,255,0.55)',   // 蓝
  'rgba(230,162,60,0.55)',   // 黄
  'rgba(155,89,182,0.55)',   // 紫
  'rgba(26,188,156,0.55)',   // 青
  'rgba(255,87,34,0.55)',    // 橙
  'rgba(144,147,153,0.55)',  // 灰
]
function colorOf(catId: number | null | undefined): string {
  if (catId == null) return PALETTE[0]
  return PALETTE[Math.abs(Number(catId)) % PALETTE.length]
}

// 监听 categories, 默认选第一个
watch(
  () => props.categories,
  (cats) => {
    if (brushCategoryId.value == null && cats.length > 0) {
      brushCategoryId.value = cats[0].id
    }
  },
  { immediate: true }
)

// ============== 加载原图 + 已有 mask ==============
watch(
  () => [props.imageUrl, props.initialMaskUrl],
  () => loadAll()
)
onMounted(() => loadAll())

function loadAll() {
  if (!props.imageUrl) return
  imgEl.crossOrigin = 'anonymous'
  imgEl.onload = () => {
    const maxW = 600
    const scale = Math.min(1, maxW / (imgEl.naturalWidth || props.imageWidth || 1))
    canvasSize.value = {
      w: Math.round((imgEl.naturalWidth || props.imageWidth) * scale),
      h: Math.round((imgEl.naturalHeight || props.imageHeight) * scale),
    }
    nextTick(() => {
      drawImage()
      loadMask()
    })
  }
  imgEl.onerror = () => ElMessage.error('图片加载失败')
  imgEl.src = props.imageUrl
}

function loadMask() {
  const c = maskCanvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, c.width, c.height)
  if (!props.initialMaskUrl) {
    initial.value = ''
    return
  }
  maskEl.crossOrigin = 'anonymous'
  maskEl.onload = () => {
    ctx.clearRect(0, 0, c.width, c.height)
    ctx.drawImage(maskEl, 0, 0, c.width, c.height)
    // 记录初始 PNG 哈希, 用于 dirty 检测
    initial.value = canvasToDataUrl(c)
    dirty.value = false
  }
  maskEl.onerror = () => {
    initial.value = ''
  }
  maskEl.src = props.initialMaskUrl
}

function drawImage() {
  const c = imgCanvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, c.width, c.height)
  if (imgEl.complete && imgEl.naturalWidth > 0) {
    ctx.drawImage(imgEl, 0, 0, c.width, c.height)
  } else {
    ctx.fillStyle = '#f5f5f5'
    ctx.fillRect(0, 0, c.width, c.height)
  }
}

// ============== 鼠标事件: 画刷 / 橡皮 ==============
const painting = ref(false)
function eventToCanvas(e: MouseEvent): { x: number; y: number } {
  if (!maskCanvasRef.value) return { x: 0, y: 0 }
  const rect = maskCanvasRef.value.getBoundingClientRect()
  return {
    x: Math.round((e.clientX - rect.left) * (maskCanvasRef.value.width / rect.width)),
    y: Math.round((e.clientY - rect.top) * (maskCanvasRef.value.height / rect.height)),
  }
}
function onMouseDown(e: MouseEvent) {
  if (mode.value === 'pan') return
  if (mode.value === 'brush' && brushCategoryId.value == null) {
    ElMessage.warning('请先在调色板选一个类别')
    return
  }
  painting.value = true
  paintAt(eventToCanvas(e))
}
function onMouseMove(e: MouseEvent) {
  if (!painting.value) return
  paintAt(eventToCanvas(e))
}
function onMouseUp() { painting.value = false }

function paintAt(p: { x: number; y: number }) {
  const c = maskCanvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  if (mode.value === 'erase') {
    ctx.globalCompositeOperation = 'destination-out'
    ctx.fillStyle = 'rgba(0,0,0,1)'
    ctx.beginPath()
    ctx.arc(p.x, p.y, brushSize.value, 0, Math.PI * 2)
    ctx.fill()
    ctx.globalCompositeOperation = 'source-over'
  } else {
    // brush: 用当前类别颜色画圆
    ctx.fillStyle = colorOf(brushCategoryId.value)
    ctx.beginPath()
    ctx.arc(p.x, p.y, brushSize.value, 0, Math.PI * 2)
    ctx.fill()
  }
  dirty.value = true
}

// ============== mask 统计 / 保存 ==============
const maskStats = computed(() => {
  const c = maskCanvasRef.value
  if (!c || c.width === 0) return null
  const ctx = c.getContext('2d')
  if (!ctx) return null
  const data = ctx.getImageData(0, 0, c.width, c.height).data
  let painted = 0
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] > 0) painted++
  }
  return { painted, total: c.width * c.height }
})

function clearMask() {
  ElMessageBox.confirm('清空当前 mask?', '确认', {
    type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消',
  }).then(() => {
    const c = maskCanvasRef.value
    if (!c) return
    const ctx = c.getContext('2d')
    if (ctx) ctx.clearRect(0, 0, c.width, c.height)
    dirty.value = true
    emit('clear')
  }).catch(() => {})
}

function canvasToDataUrl(c: HTMLCanvasElement): string {
  return c.toDataURL('image/png')
}

function onSave() {
  const c = maskCanvasRef.value
  if (!c) return
  c.toBlob((blob) => {
    if (!blob) { ElMessage.error('mask 生成失败'); return }
    const file = new File([blob], `mask_${props.imageId}.png`, { type: 'image/png' })
    initial.value = canvasToDataUrl(c)
    dirty.value = false
    emit('save', file)
  }, 'image/png')
}

// 渲染: 当 size 变化时重绘
watch(canvasSize, () => { drawImage(); loadMask() })

// 释放资源
onBeforeUnmount(() => {
  imgEl.onload = null
  imgEl.onerror = null
  maskEl.onload = null
  maskEl.onerror = null
})

// 强制 re-export (TS 提示)
export {}
</script>

<style scoped>
.seg-annotator {
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
.brush-size {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: #606266;
}
.size-num {
  width: 40px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: #909399;
  font-size: 12px;
}
.palette {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  font-size: 13px;
  color: #606266;
}
.palette-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 2px solid #ebeef5;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  background: #fff;
  user-select: none;
}
.palette-item.active {
  border-width: 2px;
  background: #f0f9ff;
}
.cat-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  vertical-align: middle;
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
.layer {
  display: block;
  max-width: 100%;
}
.layer.interactive {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  cursor: crosshair;
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
.meta {
  font-size: 12px;
  color: #909399;
}
</style>

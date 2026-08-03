<script setup lang="ts">
/**
 * DatasetImageList - 图像列表视图 (紧凑行式布局)
 *
 * v3.x 重构: 使用 div 行式布局替代 el-table
 * - 原因: el-table 通过 slot 渲染单元格, <style scoped> 的 data-v 属性可能
 *   无法正确穿透, 导致行高/列宽样式失效, 出现缩略图被拉伸的渲染异常
 * - 改用 div + flex 行式布局, 样式作用域可控, 渲染稳定
 *
 * 列结构: [复选框] [缩略图 64x64] [文件名 + 元数据 + 标签] [操作]
 * 行高: 80px (padding 8 + 缩略图 64 + padding 8)
 * 1080p 屏同屏可见 5-6 行, 通过 .ds-image-area 独立滚动
 */
import { View, Delete, RefreshLeft } from '@element-plus/icons-vue'
import { imageApi } from '@/api'
import { getRejectReasonLabel } from '@/utils/rejectReason'

const props = defineProps<{
  images: any[]
  selectedIds: number[]
  statusType: (s: string) => any
  statusLabel: (s: string) => string
  confColor: (c: number) => string
  formatBytes: (b: number) => string
  hasAnnotation: (img: any) => boolean
}>()

const emit = defineEmits<{
  (e: 'toggleSelect', id: number): void
  (e: 'toggleCheckbox', id: number, checked: boolean): void
  (e: 'openViewer', id: number): void
  (e: 'clearAnnotation', img: any): void
  (e: 'deleteOne', img: any): void
  (e: 'unmarkUnqualified', img: any): void
}>()

/** 行点击: 切换选中状态 */
function onRowClick(img: any) {
  emit('toggleSelect', img.id)
}
</script>

<template>
  <div class="image-list">
    <!-- 表头: 与行结构对齐, 让用户对列位置有预期 -->
    <div class="image-list__header">
      <div class="image-list__col image-list__col--checkbox"></div>
      <div class="image-list__col image-list__col--thumb">缩略图</div>
      <div class="image-list__col image-list__col--name">文件名</div>
      <div class="image-list__col image-list__col--status">状态</div>
      <div class="image-list__col image-list__col--quality">质量</div>
      <div class="image-list__col image-list__col--label">最终类别</div>
      <div class="image-list__col image-list__col--ai">AI 预测</div>
      <div class="image-list__col image-list__col--size">尺寸</div>
      <div class="image-list__col image-list__col--bytes">大小</div>
      <div class="image-list__col image-list__col--time">上传时间</div>
      <div class="image-list__col image-list__col--actions">操作</div>
    </div>

    <!-- 数据行: 每行一个 image-list__row -->
    <div
      v-for="img in images"
      :key="img.id"
      class="image-list__row"
      :class="{
        'is-selected': selectedIds.includes(img.id),
        'is-unqualified': img.quality_flag === 'unqualified',
      }"
      @click="onRowClick(img)"
    >
      <!-- 复选框 -->
      <div class="image-list__col image-list__col--checkbox">
        <el-checkbox
          :model-value="selectedIds.includes(img.id)"
          @change="(v: any) => emit('toggleCheckbox', img.id, !!v)"
          @click.stop
        />
      </div>

      <!-- 缩略图: 固定 64x64, object-fit: cover 防止拉伸 -->
      <div class="image-list__col image-list__col--thumb">
        <div class="list-thumb">
          <img
            :src="imageApi.thumbnailUrl(img.id, 160)"
            :alt="img.filename"
            loading="lazy"
            @error="(e: any) => { e.target.src = imageApi.fileUrl(img.id) }"
          />
        </div>
      </div>

      <!-- 文件名: 单行省略, hover tooltip 显示全名 -->
      <div class="image-list__col image-list__col--name">
        <el-tooltip :content="img.filename" placement="top" :show-after="300">
          <span class="list-cell-name">{{ img.filename }}</span>
        </el-tooltip>
      </div>

      <!-- 状态 -->
      <div class="image-list__col image-list__col--status">
        <el-tag :type="statusType(img.status)" size="small" effect="plain">
          {{ statusLabel(img.status) }}
        </el-tag>
      </div>

      <!-- 质量 -->
      <div class="image-list__col image-list__col--quality">
        <el-tag
          v-if="img.quality_flag === 'unqualified'"
          type="danger" size="small" effect="dark"
        >
          {{ getRejectReasonLabel(img.reject_reason) }}
        </el-tag>
        <span v-else class="list-cell-dim">合格</span>
      </div>

      <!-- 最终类别 -->
      <div class="image-list__col image-list__col--label">
        <el-tooltip
          v-if="img.final_label_name"
          :content="img.final_label_name"
          placement="top"
        >
          <el-tag size="small" effect="dark" class="list-cell-tag">
            {{ img.final_label_name }}
          </el-tag>
        </el-tooltip>
        <span v-else class="list-cell-dim">—</span>
      </div>

      <!-- AI 预测 -->
      <div class="image-list__col image-list__col--ai">
        <template v-if="img.ai_prediction?.top1">
          <el-tag size="small">{{ img.ai_prediction.top1 }}</el-tag>
          <el-tag
            size="small"
            :color="confColor(img.ai_prediction.top1_conf || 0)"
            effect="dark"
            class="list-cell-tag"
          >
            {{ ((img.ai_prediction.top1_conf || 0) * 100).toFixed(0) }}%
          </el-tag>
        </template>
        <span v-else class="list-cell-dim">无</span>
      </div>

      <!-- 尺寸 -->
      <div class="image-list__col image-list__col--size">
        <span v-if="img.width && img.height" class="list-cell-num">
          {{ img.width }}×{{ img.height }}
        </span>
        <span v-else class="list-cell-dim">—</span>
      </div>

      <!-- 大小 -->
      <div class="image-list__col image-list__col--bytes">
        <span class="list-cell-num">{{ formatBytes(img.file_size) }}</span>
      </div>

      <!-- 上传时间 -->
      <div class="image-list__col image-list__col--time">
        <span v-if="img.created_at" class="list-cell-dim">
          {{ img.created_at.slice(0, 16).replace('T', ' ') }}
        </span>
        <span v-else class="list-cell-dim">—</span>
      </div>

      <!-- 操作: hover 显示, 默认透明 -->
      <div class="image-list__col image-list__col--actions" @click.stop>
        <el-tooltip content="查看详情" placement="top">
          <el-button
            class="list-action-btn"
            type="primary" :icon="View" size="small" circle
            @click="emit('openViewer', img.id)"
          />
        </el-tooltip>
        <el-tooltip v-if="hasAnnotation(img)" content="清除标注" placement="top">
          <el-button
            class="list-action-btn"
            type="warning" :icon="RefreshLeft" size="small" circle
            @click="emit('clearAnnotation', img)"
          />
        </el-tooltip>
        <el-tooltip v-if="img.quality_flag === 'unqualified'" content="撤销不合格" placement="top">
          <el-button
            class="list-action-btn"
            type="success" :icon="RefreshLeft" size="small" circle
            @click="emit('unmarkUnqualified', img)"
          />
        </el-tooltip>
        <el-tooltip content="删除" placement="top">
          <el-button
            class="list-action-btn"
            type="danger" :icon="Delete" size="small" circle
            @click="emit('deleteOne', img)"
          />
        </el-tooltip>
      </div>
    </div>

    <!-- 空状态 -->
    <el-empty
      v-if="images.length === 0"
      description="该筛选条件下暂无图片"
      :image-size="80"
    />
  </div>
</template>

<style scoped>
/* ====== 列表容器: 单列堆叠 ====== */
.image-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}

/* ====== 列宽统一: 12 列, 各列固定宽度 =====
 * - 与表头和行共用同一套列宽定义
 * - 缩略图 64px, 文件名弹性, 其他列按内容设定
 * - 整体宽度需在 1100px 以上才能完整展示 */
.image-list__col--checkbox { width: 40px;  flex: 0 0 40px;  }
.image-list__col--thumb    { width: 80px;  flex: 0 0 80px;  }
.image-list__col--name     { min-width: 160px; flex: 1 1 160px; }
.image-list__col--status   { width: 90px;  flex: 0 0 90px;  }
.image-list__col--quality  { width: 110px; flex: 0 0 110px; }
.image-list__col--label    { width: 120px; flex: 0 0 120px; }
.image-list__col--ai       { width: 140px; flex: 0 0 140px; }
.image-list__col--size     { width: 100px; flex: 0 0 100px; }
.image-list__col--bytes    { width: 80px;  flex: 0 0 80px;  }
.image-list__col--time     { width: 140px; flex: 0 0 140px; }
.image-list__col--actions  { width: 170px; flex: 0 0 170px; }

/* ====== 表头: 浅灰背景, 加粗字体 ======
 * - 与行结构对齐, 用户对列位置有预期
 * - sticky 固定: 表格体滚动时表头始终可见, 符合标准表格交互
 * - z-index 高于数据行, 避免被内容遮挡
 * - 背景色 + border-bottom + 阴影, 与数据行明显区分
 *
 * v3.x 关键约束 (外层 .ds-content 用 overflow: clip 而非 hidden):
 * - sticky 元素的「最近滚动祖先」必须是可以真正滚动的容器
 * - .ds-content 设了 overflow: clip (不创建 scroll container),
 *   所以 .image-list__header 的滚动祖先穿透到 .ds-image-area
 * - .ds-image-area 是 overflow-y: auto 的独立滚动区, 滚动此区域
 *   即可让表头吸顶
 * - 若 .ds-content 用 overflow: hidden, 会截胡滚动祖先, sticky 失效 */
.image-list__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg-soft);
  border: 1px solid var(--border-soft);
  border-bottom: 2px solid var(--border-strong);
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  position: sticky;
  top: 0;
  z-index: 10;
  flex-shrink: 0;
  /* 固定时投影: 增强表头与数据行的视觉分离 */
  box-shadow: 0 1px 0 var(--border-soft), 0 2px 6px rgba(0, 0, 0, 0.04);
}
.image-list__header .image-list__col {
  display: flex;
  align-items: center;
  white-space: nowrap;
  overflow: hidden;
}
.image-list__header .image-list__col--name { font-weight: 600; }

/* ====== 数据行: 卡片样式, 行高 80px ======
 * - 与筛选栏视觉一致: 圆角 + 浅边框
 * - hover: 边框加深 + 微阴影
 * - 选中: 蓝色描边
 * - 不合格: 红色微背景 */
.image-list__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-sm);
  cursor: pointer;
  user-select: none;
  transition: all 0.2s var(--ease-out);
  min-height: 80px;
  box-sizing: border-box;
}
.image-list__row:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}
.image-list__row.is-selected {
  border-color: var(--brand-primary) !important;
  box-shadow: 0 0 0 1px rgba(79, 124, 255, 0.25) !important;
  background: rgba(79, 124, 255, 0.03) !important;
}
.image-list__row.is-unqualified {
  border-color: rgba(245, 108, 108, 0.4);
  background: rgba(245, 108, 108, 0.03);
}
.image-list__row.is-unqualified.is-selected {
  border-color: var(--brand-primary) !important;
}

/* ====== 列容器通用: 垂直居中, 单行省略 ====== */
.image-list__col {
  display: flex;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  padding: 0 4px;
}

/* ====== 复选框列: 居中 ====== */
.image-list__col--checkbox {
  justify-content: center;
  padding: 0;
}

/* ====== 缩略图列: 固定 64x64 ======
 * - 关键: 用 div.list-thumb 包 img, 显式约束尺寸
 * - 防止图片被父级 (el-table cell / 行 flex 子项) 拉伸 */
.list-thumb {
  width: 64px;
  height: 64px;
  border-radius: 4px;
  overflow: hidden;
  background: linear-gradient(135deg, #f5f7 0%, #ebedf2 100%);
  flex-shrink: 0;
}
.list-thumb img {
  width: 64px;
  height: 64px;
  max-width: 64px;
  max-height: 64px;
  object-fit: cover;
  display: block;
}

/* ====== 文件名: 单行省略 ====== */
.list-cell-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
  color: var(--text-primary);
  font-size: 13px;
  width: 100%;
}

/* ====== 标签类: 防止溢出 ====== */
.list-cell-tag {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ====== 通用文本样式 ====== */
.list-cell-dim { color: var(--text-placeholder); font-size: 12px; }
.list-cell-num {
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  color: var(--text-secondary);
}

/* ====== 操作列: 按钮始终可见, 水平排列 =====
 * - flex 容器显式 row + flex-wrap: nowrap, 防止按钮换行成竖排
 * - 按钮无 opacity 动画, 始终 opacity: 1
 * - 用 el-tooltip 包裹时确保 tooltip 是 inline-flex, 不撑开宽度 */
.image-list__col--actions {
  justify-content: flex-start;
  gap: 2px;
  padding: 0 4px;
  flex-wrap: nowrap;
  overflow: visible;
}
.image-list__col--actions :deep(.el-tooltip__trigger) {
  display: inline-flex !important;
}
.list-action-btn {
  opacity: 1;
  transform: scale(0.85);
  transition: transform 0.15s var(--ease-out);
}
.image-list__row:hover .list-action-btn {
  transform: scale(1);
}

/* ====== 触屏设备: 按钮正常显示 ====== */
@media (hover: none) {
  .list-action-btn { transform: scale(1); }
}

/* ====== 响应式: 中等屏幕隐藏部分次要列 ====== */
@media (max-width: 1400px) {
  .image-list__col--quality,
  .image-list__col--size,
  .image-list__col--time { display: none; }
  .image-list__col--name { flex: 1 1 auto; }
}
@media (max-width: 1100px) {
  .image-list__col--ai { display: none; }
}
@media (max-width: 900px) {
  .image-list__col--label { display: none; }
  .image-list__col--bytes { display: none; }
}
</style>

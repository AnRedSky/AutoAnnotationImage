<script setup lang="ts">
/**
 * 缓存监控卡片 (v3.3.1 L4)
 * =========================
 * 调用 GET /api/teams/_cache/stats (仅 admin).
 * 展示 hit_rate_percent 大字 + hits/misses 数值 + 手动刷新按钮.
 *
 * 用途: 管理后台运维面板, 监控团队详情缓存命中率.
 */
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { cacheApi, type CacheStats } from '@/api'

const stats = ref<CacheStats>({
  hits: 0,
  misses: 0,
  total: 0,
  hit_rate_percent: 0,
})
const loading = ref(false)
const lastUpdated = ref<string | null>(null)

const hitRateColor = computed(() => {
  const r = stats.value.hit_rate_percent
  if (r >= 80) return '#67c23a'  // green
  if (r >= 50) return '#e6a23c'  // orange
  return '#f56c6c'              // red
})

const hitRateLevel = computed((): string => {
  const r = stats.value.hit_rate_percent
  if (r >= 80) return '优秀'
  if (r >= 50) return '良好'
  if (r > 0) return '偏低'
  return '暂无数据'
})

const loadStats = async () => {
  loading.value = true
  try {
    const res: any = await cacheApi.stats()
    stats.value = res
    lastUpdated.value = new Date().toLocaleTimeString('zh-CN')
  } catch (e: any) {
    ElMessage.error(
      '加载缓存统计失败: ' + (e?.response?.data?.detail || e?.message),
    )
  } finally {
    loading.value = false
  }
}

const fmt = (n: number) => n.toLocaleString('zh-CN')

onMounted(loadStats)
</script>

<template>
  <el-card shadow="never" class="cache-monitor-card">
    <template #header>
      <div class="card-header">
        <span class="title">缓存监控</span>
        <div class="actions">
          <span v-if="lastUpdated" class="updated-at">
            更新于 {{ lastUpdated }}
          </span>
          <el-button
            size="small"
            :loading="loading"
            @click="loadStats"
          >
            刷新
          </el-button>
        </div>
      </div>
    </template>

    <div v-loading="loading" class="content">
      <!-- 大字命中率 -->
      <div class="rate-block">
        <div class="rate-value" :style="{ color: hitRateColor }">
          {{ stats.hit_rate_percent.toFixed(1) }}<span class="percent">%</span>
        </div>
        <div class="rate-label">命中率 · {{ hitRateLevel }}</div>
      </div>

      <!-- 命中/未命中数值 -->
      <el-row :gutter="16" class="stats-row">
        <el-col :span="8">
          <div class="stat-cell">
            <div class="stat-num hits">{{ fmt(stats.hits) }}</div>
            <div class="stat-desc">命中次数</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="stat-cell">
            <div class="stat-num misses">{{ fmt(stats.misses) }}</div>
            <div class="stat-desc">未命中次数</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="stat-cell">
            <div class="stat-num total">{{ fmt(stats.total) }}</div>
            <div class="stat-desc">总请求数</div>
          </div>
        </el-col>
      </el-row>

      <!-- 进度条 -->
      <div class="bar-wrap">
        <el-progress
          :percentage="Math.min(stats.hit_rate_percent, 100)"
          :stroke-width="10"
          :color="hitRateColor"
          :show-text="false"
        />
      </div>

      <p class="footnote">
        数据来源: Redis cache:stats 计数器 (团队详情缓存)
      </p>
    </div>
  </el-card>
</template>

<style scoped>
.cache-monitor-card {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #303133);
}
.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.updated-at {
  font-size: 12px;
  color: var(--text-secondary, #909399);
}
.content {
  min-height: 140px;
  padding: 8px 0;
}
.rate-block {
  text-align: center;
  margin-bottom: 24px;
}
.rate-value {
  font-size: 48px;
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -1px;
}
.percent {
  font-size: 20px;
  font-weight: 500;
  margin-left: 4px;
  opacity: 0.7;
}
.rate-label {
  font-size: 13px;
  color: var(--text-secondary, #606266);
  margin-top: 4px;
}
.stats-row {
  margin-bottom: 16px;
}
.stat-cell {
  text-align: center;
  padding: 12px 4px;
  background: #fafbfc;
  border-radius: 4px;
}
.stat-num {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.2;
}
.stat-num.hits { color: #67c23a; }
.stat-num.misses { color: #f56c6c; }
.stat-num.total { color: #409eff; }
.stat-desc {
  font-size: 12px;
  color: var(--text-secondary, #909399);
  margin-top: 2px;
}
.bar-wrap {
  margin-top: 8px;
}
.footnote {
  font-size: 11px;
  color: var(--text-secondary, #c0c4cc);
  text-align: right;
  margin: 8px 0 0;
}
</style>

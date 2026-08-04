<script setup lang="ts">
/**
 * 共享数据集到团队弹窗 (v3.3.1)
 * ===============================
 *
 * 用于在 DatasetDetail 页面, 将当前数据集共享给指定团队。
 * 实现 v3.3.1 安全加固: 二次确认 (输入数据集名) + 列出影响范围。
 *
 * Props:
 *  - modelValue: boolean
 *  - datasetId:  number
 *  - datasetName: string
 *  - allTeams:  TeamItem[]   当前用户所在团队列表 (来自父组件)
 *  - sharedTeamIds: Set<number>  已共享的团队 id 集合
 *
 * Emits:
 *  - update:modelValue
 *  - shared    共享成功
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { teamApi, type TeamItem } from '@/api'

const props = defineProps<{
  modelValue: boolean
  datasetId: number
  datasetName: string
  allTeams: TeamItem[]
  sharedTeamIds: Set<number>
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'shared'): void
}>()

const selectedTeamId = ref<number | undefined>()
const confirmText = ref('')
const submitting = ref(false)

/** 排除已共享的团队 */
const availableTeams = computed(() =>
  props.allTeams.filter((t) => !props.sharedTeamIds.has(t.id))
)

/** 选中团队的对象 (用于展示影响范围) */
const selectedTeam = computed(() =>
  props.allTeams.find((t) => t.id === selectedTeamId.value) || null
)

/** 是否可提交: 选了团队 + 确认文字精确匹配 */
const canSubmit = computed(
  () => !!selectedTeamId.value && confirmText.value.trim() === props.datasetName
)

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      selectedTeamId.value = undefined
      confirmText.value = ''
    }
  }
)

const close = () => emit('update:modelValue', false)

const onSubmit = async () => {
  if (!canSubmit.value || !selectedTeamId.value) return
  submitting.value = true
  try {
    await teamApi.shareDataset(props.datasetId, selectedTeamId.value)
    ElMessage.success(`已共享给「${selectedTeam.value?.name}」`)
    emit('shared')
    close()
  } catch (e: any) {
    ElMessage.error('共享失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="共享数据集到团队"
    width="500px"
    @close="close"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px">
      <template #title>
        <strong>共享后的影响</strong>
      </template>
      团队下所有 <code>editor</code> 角色成员将对本数据集获得 <code>editor</code> 权限，
      <code>viewer</code> 角色获得只读权限。取消共享后权限立即回收。
    </el-alert>

    <el-form label-position="top">
      <el-form-item label="选择目标团队">
        <el-select
          v-model="selectedTeamId"
          filterable
          placeholder="仅可选择我所在且尚未共享的团队"
          style="width: 100%"
        >
          <el-option
            v-for="t in availableTeams"
            :key="t.id"
            :label="`${t.name} (${t.my_role})`"
            :value="t.id"
          />
        </el-select>
        <div v-if="!availableTeams.length" class="form-hint">
          没有可共享的团队（所有团队已共享此数据集，或您尚未加入任何团队）
        </div>
      </el-form-item>

      <el-form-item v-if="selectedTeam" label="影响范围">
        <ul class="impact-list">
          <li>团队名: <strong>{{ selectedTeam.name }}</strong></li>
          <li>您的角色: {{ selectedTeam.my_role }}</li>
          <li>成员上限: {{ selectedTeam.max_members }} 人</li>
        </ul>
      </el-form-item>

      <el-form-item label="二次确认">
        <p class="confirm-hint">
          请输入数据集全名 <code>{{ datasetName }}</code> 以确认操作:
        </p>
        <el-input v-model="confirmText" :placeholder="datasetName" />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button
        type="primary"
        :disabled="!canSubmit"
        :loading="submitting"
        @click="onSubmit"
      >
        确认共享
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.form-hint {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}
.confirm-hint {
  margin: 0 0 8px 0;
  font-size: 13px;
  color: var(--text-secondary);
}
.confirm-hint code {
  background: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: 3px;
  font-weight: 600;
  color: var(--color-warning);
}
.impact-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--text-regular);
}
.impact-list li {
  margin: 4px 0;
}
</style>

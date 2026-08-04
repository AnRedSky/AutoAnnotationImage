<script setup lang="ts">
/**
 * 修改成员角色弹窗 (v3.3.1)
 * ==========================
 */
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { teamApi, type TeamMemberItem } from '@/api'

const props = defineProps<{
  modelValue: boolean
  teamId: number
  member: TeamMemberItem | null
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'updated'): void
}>()

const newRole = ref('editor')
const submitting = ref(false)

const teamRoles = [
  { label: '可管理', value: 'manager', desc: '管理成员 + 配置数据集权限 + 编辑标注' },
  { label: '可编辑', value: 'editor', desc: '可对共享数据集进行标注' },
  { label: '仅阅读', value: 'viewer', desc: '只读' },
]

watch(
  () => props.modelValue,
  (open) => {
    if (open && props.member) newRole.value = props.member.role
  }
)

const roleLabel = (r: string) =>
  teamRoles.find((x) => x.value === r)?.label || r
const roleTagType = (r: string) =>
  r === 'manager' ? 'warning' : r === 'editor' ? 'success' : 'info'

const close = () => emit('update:modelValue', false)

const onSubmit = async () => {
  if (!props.member) return
  if (newRole.value === props.member.role) {
    close()
    return
  }
  submitting.value = true
  try {
    await teamApi.updateMemberRole(props.teamId, props.member.user_id, newRole.value)
    ElMessage.success('角色修改成功')
    emit('updated')
    close()
  } catch (e: any) {
    ElMessage.error('修改失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="修改成员角色"
    width="440px"
    @close="close"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div v-if="member" class="role-dialog-body">
      <p>
        用户: <strong>{{ member.username }}</strong>
      </p>
      <p>
        当前角色:
        <el-tag :type="roleTagType(member.role)" size="small">
          {{ roleLabel(member.role) }}
        </el-tag>
      </p>
      <el-divider />
      <el-radio-group v-model="newRole" class="role-radio-group">
        <el-radio v-for="r in teamRoles" :key="r.value" :value="r.value" class="role-radio">
          <span class="role-name">{{ r.label }}</span>
          <span class="role-desc">{{ r.desc }}</span>
        </el-radio>
      </el-radio-group>
    </div>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="onSubmit">确认</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.role-radio-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.role-radio {
  display: flex;
  align-items: center;
}
.role-name {
  font-weight: 500;
  margin-right: 8px;
}
.role-desc {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>

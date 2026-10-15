<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import ModalShell from '@/components/common/ModalShell.vue'
import Avatar from '@/components/common/Avatar.vue'
import { fetchPersonaJob, fetchSettings, generatePersona, saveSettings, startGeneratePersonas, testPreset, type AiPersona, type AppSettings, type ModelPreset } from '@/api/settings'
import { showToast } from '@/composables/toast'
import { bgm } from '@/composables/useBgm'

const emit = defineEmits<{ close: [] }>()

const settings = ref<AppSettings>({ presets: [], personas: [], boards: [] })
const editingPreset = ref<ModelPreset | null>(null)
const editingPersona = ref<AiPersona | null>(null)
const presetTesting = ref(false)
const genHint = ref('')
const generating = ref(false)
const quickOpen = ref(false)
const quickCount = ref(3)
const quickPresetId = ref('')
const quickBusy = ref(false)
// 生成改在服务端后台执行：轮询增量上屏，关弹窗/切页不中断
const quickAdded = ref<AiPersona[]>([])
let quickTimer: number | null = null

onMounted(load)

async function load() {
  try {
    settings.value = await fetchSettings()
  } catch (e) {
    showToast((e as Error).message)
  }
}

function blankPreset(): ModelPreset {
  return {
    id: `p-${Date.now().toString(36)}`,
    name: '',
    base_url: 'https://api.openai.com/v1',
    api_key: '',
    model: '',
    speech_temperature: 0.9,
    decision_temperature: 0.3,
    speech_reasoning_effort: 'medium',
    decision_reasoning_effort: 'low',
  }
}

function blankPersona(): AiPersona {
  return { id: `a-${Date.now().toString(36)}`, name: '', avatar: '', persona: '', preset_id: settings.value.presets[0]?.id ?? '' }
}

const GEN_PRESET_KEY = 'ww:gen-preset-id'

// 生成用模型默认取上次成功使用过的（可能是任一预设），避免第一个预设失效后生成功能跟着废掉
function rememberedGenPreset(): string {
  const saved = localStorage.getItem(GEN_PRESET_KEY) ?? ''
  if (saved && settings.value.presets.some((p) => p.id === saved)) return saved
  return settings.value.presets[0]?.id ?? ''
}

function rememberGenPreset(id: string) {
  try {
    localStorage.setItem(GEN_PRESET_KEY, id)
  } catch {
    /* 忽略存储失败 */
  }
}

const genPresetId = ref('')

function editPreset(p?: ModelPreset) {
  editingPreset.value = reactive(p ? { ...p } : blankPreset())
}

function editPersona(p?: AiPersona) {
  genHint.value = ''
  generating.value = false
  editingPersona.value = reactive(p ? { ...p } : blankPersona())
  genPresetId.value = p?.preset_id && settings.value.presets.some((x) => x.id === p.preset_id) ? p.preset_id : rememberedGenPreset()
}

async function generateInto() {
  const a = editingPersona.value
  if (!a || generating.value) return
  if (!genPresetId.value) {
    showToast('生成需要先选择一个可用的模型预设')
    return
  }
  generating.value = true
  try {
    const r = await generatePersona({ preset_id: genPresetId.value, hint: genHint.value.trim() || undefined })
    a.name = r.name
    a.persona = r.persona
    genHint.value = ''
    rememberGenPreset(genPresetId.value)
    showToast('已生成，可继续手动修改')
  } catch (e) {
    showToast((e as Error).message)
  } finally {
    generating.value = false
  }
}

function openQuick() {
  if (!settings.value.presets.length) {
    showToast('先在上方添加一个模型预设，才能一键生成玩家')
    return
  }
  if (!quickPresetId.value || !settings.value.presets.some((p) => p.id === quickPresetId.value)) {
    quickPresetId.value = rememberedGenPreset()
  }
  quickOpen.value = true
}

async function quickAdd() {
  if (quickBusy.value) return
  if (!quickPresetId.value) {
    showToast('请选择用于生成的模型预设')
    return
  }
  quickBusy.value = true
  quickAdded.value = []
  try {
    await startGeneratePersonas({ preset_id: quickPresetId.value, count: quickCount.value })
    rememberGenPreset(quickPresetId.value)
    if (quickTimer === null) quickTimer = window.setInterval(pollQuick, 1200)
  } catch (e) {
    quickBusy.value = false
    showToast((e as Error).message)
  }
}

async function pollQuick() {
  try {
    const st = await fetchPersonaJob()
    for (let i = quickAdded.value.length; i < st.added.length; i++) settings.value.personas.push(st.added[i])
    quickAdded.value = st.added
    if (st.status === 'done' || st.status === 'error') {
      stopQuickPoll()
      quickBusy.value = false
      if (st.status === 'done') {
        quickOpen.value = false
        showToast(`已添加 ${st.added.length}/${st.total} 名随机玩家`)
      } else {
        showToast(st.error || '生成失败，请重试')
      }
    }
  } catch {
    /* 单次轮询失败忽略，下个周期重试 */
  }
}

function stopQuickPoll() {
  if (quickTimer !== null) {
    clearInterval(quickTimer)
    quickTimer = null
  }
}

function closeQuick() {
  // 关弹窗只停止本页轮询，服务端任务照常跑完并落盘，下次打开设置可见
  quickOpen.value = false
  stopQuickPoll()
  quickBusy.value = false
}

onUnmounted(stopQuickPoll)

async function persist() {
  try {
    settings.value = await saveSettings(settings.value)
    return true
  } catch (e) {
    showToast((e as Error).message)
    return false
  }
}

async function savePreset() {
  const p = editingPreset.value
  if (!p) return
  if (!p.name.trim() || !p.base_url.trim() || !p.model.trim()) {
    showToast('名称、Base URL、模型名不能为空')
    return
  }
  const i = settings.value.presets.findIndex((x) => x.id === p.id)
  if (i >= 0) settings.value.presets[i] = { ...p }
  else settings.value.presets.push({ ...p })
  if (await persist()) {
    editingPreset.value = null
    showToast('已保存')
  }
}

async function savePersona() {
  const a = editingPersona.value
  if (!a) return
  if (!a.name.trim()) {
    showToast('AI 玩家名字不能为空')
    return
  }
  if (!a.preset_id) {
    showToast('请先选择一个模型预设')
    return
  }
  const i = settings.value.personas.findIndex((x) => x.id === a.id)
  if (i >= 0) settings.value.personas[i] = { ...a }
  else settings.value.personas.push({ ...a })
  if (await persist()) {
    editingPersona.value = null
    showToast('已保存')
  }
}

async function removePreset(p: ModelPreset) {
  if (settings.value.personas.some((a) => a.preset_id === p.id)) {
    showToast('有 AI 玩家正在使用该预设，先解除绑定')
    return
  }
  settings.value.presets = settings.value.presets.filter((x) => x.id !== p.id)
  await persist()
}

const batchOpen = ref(false)
const batchPresetId = ref('')
const batchSelected = ref<string[]>([])

function openBatch(p: ModelPreset) {
  batchPresetId.value = p.id
  batchSelected.value = []
  batchOpen.value = true
}

function toggleAllBatch() {
  const all = settings.value.personas
  batchSelected.value = batchSelected.value.length === all.length ? [] : all.map((a) => a.id)
}

async function applyBatch() {
  if (!batchPresetId.value || !batchSelected.value.length) {
    showToast('请先勾选要应用的 AI 玩家')
    return
  }
  const ids = new Set(batchSelected.value)
  for (const a of settings.value.personas) {
    if (ids.has(a.id)) a.preset_id = batchPresetId.value
  }
  if (await persist()) {
    batchOpen.value = false
    showToast(`已为 ${ids.size} 名玩家应用「${presetName(batchPresetId.value)}」`)
  }
}

async function removePersona(a: AiPersona) {
  settings.value.personas = settings.value.personas.filter((x) => x.id !== a.id)
  await persist()
}

async function testCurrent() {
  const p = editingPreset.value
  if (!p) return
  presetTesting.value = true
  try {
    const r = await testPreset({ base_url: p.base_url, api_key: p.api_key, model: p.model })
    showToast(`连接成功：${r.message}`)
  } catch (e) {
    showToast((e as Error).message)
  } finally {
    presetTesting.value = false
  }
}

function presetName(id: string): string {
  return settings.value.presets.find((p) => p.id === id)?.name ?? '（未绑定模型）'
}
</script>

<template>
  <ModalShell title="设置" width="min(820px, calc(100vw - 48px))" @close="emit('close')">
    <div class="settings-body">
      <!-- 背景音乐 -->
      <section class="card">
        <div class="card-head">
          <h2>背景音乐</h2>
        </div>
        <p class="hint">对局内按昼夜自动切换曲目；麦克风录音时自动静音，避免干扰语音识别。</p>
        <label class="bgm-row">
          <input v-model="bgm.enabled" type="checkbox" />
          <span>开启背景音乐</span>
          <input
            v-model.number="bgm.volume"
            class="bgm-vol"
            type="range"
            min="0"
            max="1"
            step="0.02"
            :disabled="!bgm.enabled"
          />
          <span class="bgm-pct">{{ Math.round(bgm.volume * 100) }}%</span>
        </label>
        <p class="hint attribution">音乐：Midnight Tale（大厅）/ Crypto（白天）/ Darkling（夜晚） — Kevin MacLeod (incompetech.com)，Licensed under CC BY 4.0</p>
      </section>

      <!-- 模型预设 -->
      <section class="card">
        <div class="card-head">
          <h2>模型预设</h2>
          <button class="btn primary" @click="editPreset()">＋ 新增预设</button>
        </div>
        <p class="hint">AI 玩家通过 OpenAI 兼容接口调用模型；不同玩家可以绑定不同预设（不同模型/厂商），让对局更有差异。</p>
        <div v-if="settings.presets.length === 0" class="empty">还没有模型预设，点击右上角新增。</div>
        <div v-for="p in settings.presets" :key="p.id" class="row">
          <div class="row-main">
            <div class="row-title">{{ p.name }}</div>
            <div class="row-sub">{{ p.model }} · {{ p.base_url }}</div>
          </div>
          <button class="btn" @click="openBatch(p)">批量应用</button>
          <button class="btn" @click="editPreset(p)">编辑</button>
          <button class="btn danger" @click="removePreset(p)">删除</button>
        </div>
      </section>

      <!-- AI 玩家 -->
      <section class="card">
        <div class="card-head">
          <h2>AI 玩家 <span class="count-chip">{{ settings.personas.length }}</span></h2>
          <div class="head-actions">
            <button class="btn" @click="openQuick">⚡ 快速添加</button>
            <button class="btn primary" @click="editPersona()">＋ 新增玩家</button>
          </div>
        </div>
        <p class="hint">每个 AI 玩家 = 名字 + 性格人设 + 模型预设。新增时可用一句话让 AI 生成名字与人设，或用「快速添加」一键生成一批随机玩家。</p>
        <div v-if="settings.personas.length === 0" class="empty">还没有 AI 玩家，点击右上角新增。</div>
        <div v-for="a in settings.personas" :key="a.id" class="row">
          <Avatar :name="a.name" :avatar="a.avatar" :size="34" />
          <div class="row-main">
            <div class="row-title">{{ a.name }}</div>
            <div class="row-sub">{{ presetName(a.preset_id) }} · {{ a.persona || '（无人设）' }}</div>
          </div>
          <button class="btn" @click="editPersona(a)">编辑</button>
          <button class="btn danger" @click="removePersona(a)">删除</button>
        </div>
      </section>
    </div>

    <!-- 预设编辑弹窗 -->
    <Teleport to="body">
      <div v-if="editingPreset" class="modal-mask" @click.self="editingPreset = null">
        <div class="modal">
          <div class="modal-title">{{ settings.presets.some((x) => x.id === editingPreset!.id) ? '编辑模型预设' : '新增模型预设' }}</div>
          <label class="field">
            <span>名称</span>
            <input v-model="editingPreset.name" class="input" placeholder="例如：GPT-4o / DeepSeek" />
          </label>
          <label class="field">
            <span>Base URL</span>
            <input v-model="editingPreset.base_url" class="input" placeholder="https://api.openai.com/v1" />
          </label>
          <label class="field">
            <span>API Key</span>
            <input v-model="editingPreset.api_key" class="input" type="password" placeholder="sk-..." />
          </label>
          <label class="field">
            <span>模型名</span>
            <input v-model="editingPreset.model" class="input" placeholder="gpt-4o" />
          </label>
          <div class="field-pair">
            <label class="field">
              <span>发言温度</span>
              <input v-model.number="editingPreset.speech_temperature" type="number" step="0.1" min="0" max="2" class="input" />
            </label>
            <label class="field">
              <span>决策温度</span>
              <input v-model.number="editingPreset.decision_temperature" type="number" step="0.1" min="0" max="2" class="input" />
            </label>
          </div>
          <div class="field-pair">
            <label class="field">
              <span>发言思考力度</span>
              <select v-model="editingPreset.speech_reasoning_effort" class="input">
                <option value="low">低</option>
                <option value="medium">中</option>
                <option value="high">高</option>
              </select>
            </label>
            <label class="field">
              <span>决策思考力度</span>
              <select v-model="editingPreset.decision_reasoning_effort" class="input">
                <option value="low">低</option>
                <option value="medium">中</option>
                <option value="high">高</option>
              </select>
            </label>
          </div>
          <p class="hint">思考力度控制推理型模型的思考深度：越低节奏越快、消耗越少，但内容可能变简单。发言建议「中」，投票等决策建议「低」。</p>
          <div class="modal-foot">
            <button class="btn" :disabled="presetTesting" @click="testCurrent">{{ presetTesting ? '测试中…' : '测试连接' }}</button>
            <span class="foot-space"></span>
            <button class="btn" @click="editingPreset = null">取消</button>
            <button class="btn primary" @click="savePreset">保存</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- AI 玩家编辑弹窗 -->
    <Teleport to="body">
      <div v-if="editingPersona" class="modal-mask" @click.self="editingPersona = null">
        <div class="modal">
          <div class="modal-title">{{ settings.personas.some((x) => x.id === editingPersona!.id) ? '编辑 AI 玩家' : '新增 AI 玩家' }}</div>
          <input
            v-model="genHint"
            class="input"
            maxlength="60"
            placeholder="一句话设定（可选）：例如 老谋深算的骗子"
            @keyup.enter="generateInto"
          />
          <div class="gen-row">
            <select v-model="genPresetId" class="input gen-preset" title="生成所用的模型，与下方绑定预设相互独立">
              <option value="" disabled>生成用模型</option>
              <option v-for="p in settings.presets" :key="p.id" :value="p.id">{{ p.name }}（{{ p.model }}）</option>
            </select>
            <span class="foot-space"></span>
            <button class="btn gen-btn" :disabled="generating || !genPresetId" @click="generateInto">
              {{ generating ? '生成中…' : '✨ AI 生成' }}
            </button>
          </div>
          <p class="gen-hint">
            {{
              genPresetId
                ? '生成使用上方选择的模型（记住上次成功使用的）；不填设定则随机生成，生成后可再手动修改'
                : '生成需要先选择一个可用的生成模型'
            }}
          </p>
          <label class="field">
            <span>名字</span>
            <input v-model="editingPersona.name" class="input" maxlength="10" placeholder="例如：老狐狸，也可以用上方 AI 生成" />
          </label>
          <label class="field">
            <span>性格人设</span>
            <textarea v-model="editingPersona.persona" class="input persona-text" rows="5" placeholder="例如：老练谨慎，说话滴水不漏，擅长煽动和伪装"></textarea>
          </label>
          <label class="field">
            <span>模型预设</span>
            <select v-model="editingPersona.preset_id" class="input">
              <option value="" disabled>请选择</option>
              <option v-for="p in settings.presets" :key="p.id" :value="p.id">{{ p.name }}（{{ p.model }}）</option>
            </select>
          </label>
          <div class="modal-foot">
            <span class="foot-space"></span>
            <button class="btn" @click="editingPersona = null">取消</button>
            <button class="btn primary" @click="savePersona">保存</button>
          </div>
        </div>
      </div>
    </Teleport>
    <!-- 快速添加弹窗 -->
    <Teleport to="body">
      <div v-if="quickOpen" class="modal-mask" @click.self="closeQuick">
        <div class="modal quick-modal">
          <div class="modal-title">⚡ 快速添加随机玩家</div>
          <p class="hint">模型将并行生成指定数量的随机 AI 玩家（名字与人设互不重复），生成一个上屏一个并自动保存。每次最多 5 名，可多次添加。</p>
          <label class="field">
            <span>数量</span>
            <select v-model.number="quickCount" class="input" :disabled="quickBusy">
              <option v-for="n in 5" :key="n" :value="n">{{ n }} 名</option>
            </select>
          </label>
          <label class="field">
            <span>生成用模型（新玩家将绑定它）</span>
            <select v-model="quickPresetId" class="input" :disabled="quickBusy">
              <option value="" disabled>请选择</option>
              <option v-for="p in settings.presets" :key="p.id" :value="p.id">{{ p.name }}（{{ p.model }}）</option>
            </select>
          </label>
          <div v-if="quickBusy" class="quick-progress">
            <template v-if="quickAdded.length">
              已生成 {{ quickAdded.length }}/{{ quickCount }} 名：{{ quickAdded.map((p) => p.name).join('、') }}
            </template>
            <template v-else>正在生成，通常每人十几秒……</template>
          </div>
          <div class="modal-foot">
            <span class="foot-space"></span>
            <button class="btn" @click="closeQuick">{{ quickBusy ? '后台继续' : '取消' }}</button>
            <button class="btn primary" :disabled="quickBusy" @click="quickAdd">{{ quickBusy ? '生成中…' : '生成并添加' }}</button>
          </div>
        </div>
      </div>
    </Teleport>
    <!-- 批量应用预设弹窗 -->
    <Teleport to="body">
      <div v-if="batchOpen" class="modal-mask" @click.self="batchOpen = false">
        <div class="modal batch-modal">
          <div class="modal-title">批量应用模型预设</div>
          <label class="field">
            <span>目标预设</span>
            <select v-model="batchPresetId" class="input">
              <option value="" disabled>请选择</option>
              <option v-for="p in settings.presets" :key="p.id" :value="p.id">{{ p.name }}（{{ p.model }}）</option>
            </select>
          </label>
          <div class="batch-head">
            <span>选择 AI 玩家（{{ batchSelected.length }}/{{ settings.personas.length }}）</span>
            <button class="btn" @click="toggleAllBatch">
              {{ batchSelected.length === settings.personas.length && settings.personas.length > 0 ? '全不选' : '全选' }}
            </button>
          </div>
          <div v-if="settings.personas.length === 0" class="empty">还没有 AI 玩家，先在下方新增。</div>
          <div v-else class="batch-list">
            <label v-for="a in settings.personas" :key="a.id" class="batch-item">
              <input v-model="batchSelected" type="checkbox" :value="a.id" />
              <Avatar :name="a.name" :avatar="a.avatar" :size="26" />
              <span class="batch-name">{{ a.name }}</span>
              <span class="batch-preset" :class="{ same: a.preset_id === batchPresetId }">{{ presetName(a.preset_id) }}</span>
            </label>
          </div>
          <div class="modal-foot">
            <span class="foot-space"></span>
            <button class="btn" @click="batchOpen = false">取消</button>
            <button class="btn primary" :disabled="!batchSelected.length || !batchPresetId" @click="applyBatch">
              应用（{{ batchSelected.length }}）
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </ModalShell>
</template>

<style scoped lang="scss">
.settings-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  width: 100%;
  box-sizing: border-box;
  padding: $spacing-xl $spacing-xxl 40px;
  display: flex;
  flex-direction: column;
  gap: $spacing-lg;
}

.card {
  background: $bg-panel;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  padding: $spacing-xl;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;

  h2 {
    font-size: $font-size-md;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.count-chip {
  font-size: $font-size-xs;
  line-height: 18px;
  padding: 0 8px;
  border-radius: 999px;
  color: var(--c-primary);
  background: rgba(var(--c-primary-rgb), 0.12);
  border: 1px solid rgba(var(--c-primary-rgb), 0.4);
  font-weight: 600;
}

.head-actions {
  display: flex;
  gap: 8px;
}

.quick-modal {
  width: 380px;
}

.quick-progress {
  font-size: $font-size-sm;
  color: $night-accent;
  padding: 6px 10px;
  border-radius: $radius-md;
  background: rgba(var(--c-primary-rgb), 0.08);
  animation: quick-pulse 1.6s ease-in-out infinite;
  word-break: break-all;
}

@keyframes quick-pulse {
  0%,
  100% {
    opacity: 0.65;
  }
  50% {
    opacity: 1;
  }
}

.batch-modal {
  width: 420px;
}

.batch-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: $spacing-sm 0 $spacing-xs;
  font-size: $font-size-sm;
  color: $text-secondary;
}

.batch-list {
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid $border-color;
  border-radius: $radius-md;
  margin-bottom: $spacing-md;
}

.batch-item {
  display: flex;
  align-items: center;
  gap: $spacing-sm;
  padding: $spacing-xs $spacing-sm;
  cursor: pointer;
  border-bottom: 1px solid $border-light;

  &:hover {
    background: rgba(255, 255, 255, 0.03);
  }

  &:last-child {
    border-bottom: none;
  }
}

.batch-name {
  font-size: $font-size-sm;
}

.batch-preset {
  margin-left: auto;
  font-size: $font-size-xs;
  color: $text-tertiary;

  &.same {
    color: $text-secondary;
  }
}

.hint {
  color: $text-tertiary;
  font-size: $font-size-xs;
  margin: 6px 0 $spacing-sm;
}

.bgm-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: $font-size-base;
  cursor: pointer;
  padding: 4px 0;
}

.bgm-vol {
  flex: 1;
  max-width: 260px;
  margin-left: 8px;
}

.bgm-pct {
  font-size: $font-size-sm;
  color: $text-secondary;
  min-width: 38px;
  font-variant-numeric: tabular-nums;
}

.attribution {
  margin-top: 2px;
  margin-bottom: 0;
  opacity: 0.8;
}

.empty {
  color: $text-secondary;
  font-size: $font-size-sm;
  padding: $spacing-md 0;
}

.row {
  display: flex;
  align-items: center;
  gap: $spacing-md;
  padding: $spacing-sm $spacing-sm;
  margin: 0 (-$spacing-sm);
  border-radius: $radius-md;
  border-bottom: 1px solid $border-light;
  transition: background $transition-fast;

  &:hover {
    background: rgba(255, 255, 255, 0.03);
  }

  &:last-child {
    border-bottom: none;
  }
}

.row-main {
  flex: 1;
  min-width: 0;
}

.row-title {
  font-size: $font-size-base;
}

.row-sub {
  font-size: $font-size-xs;
  color: $text-secondary;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(8, 6, 16, 0.68);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 310;
}

.modal {
  width: 460px;
  max-height: 86vh;
  overflow-y: auto;
  background: $bg-panel;
  border: 1px solid var(--c-border);
  border-radius: $radius-lg;
  padding: $spacing-xl;
  display: flex;
  flex-direction: column;
  gap: $spacing-md;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(var(--c-primary-rgb), 0.08);
}

.modal-title {
  font-size: $font-size-lg;
  font-weight: 600;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;

  > span {
    color: $text-secondary;
    font-size: $font-size-sm;
  }
}

.field-pair {
  display: flex;
  gap: $spacing-md;
}

.persona-text {
  resize: vertical;
}

.gen-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.gen-preset {
  flex: 1 1 auto;
  min-width: 110px;
}

.gen-btn {
  flex-shrink: 0;
  white-space: nowrap;
}

.gen-hint {
  font-size: $font-size-xs;
  color: $text-tertiary;
  margin-top: -8px;
}

.modal-foot {
  display: flex;
  gap: 8px;
  align-items: center;
}

.foot-space {
  flex: 1;
}
</style>

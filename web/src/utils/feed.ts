/** 事件 → 消息流条目的映射（实时 SSE 与复盘共用）。 */

export interface FeedItem {
  id: string
  kind: 'host' | 'speech' | 'private' | 'night' | 'vote' | 'voteLive' | 'thinking' | 'hunter' | 'gameover' | 'system'
  seat?: number
  name?: string
  avatar?: string
  text: string
  tag?: string
  streaming?: boolean
  seq?: number
  target?: number
  reason?: string
  /** 与该条目有结构化关联的座位（行动者/目标/死者/投票双方），复盘聚光灯用 */
  related?: number[]
}

export const PHASE_LABELS: Record<string, string> = {
  lobby: '等待开始',
  night_guard: '夜晚 · 守卫',
  night_wolf: '夜晚 · 狼人行动',
  night_seer: '夜晚 · 预言家',
  night_witch_save: '夜晚 · 女巫',
  night_witch_poison: '夜晚 · 女巫',
  dawn: '天亮结算',
  sheriff_election: '警长竞选',
  day_speech: '白天 · 发言',
  vote: '白天 · 投票',
  vote_result: '投票结果',
  game_over: '游戏结束',
}

export const ROLE_LABELS: Record<string, string> = {
  werewolf: '狼人',
  villager: '村民',
  seer: '预言家',
  witch: '女巫',
  hunter: '猎人',
  idiot: '白痴',
  guard: '守卫',
}

let feedSeq = 0

export function feedId(): string {
  return `f${Date.now().toString(36)}-${++feedSeq}`
}

/** 把一条持久化事件（含 payload 展开字段）转为消息流条目；返回 null 表示不需要展示。 */
export function mapEvent(e: Record<string, any>): FeedItem | null {
  const type = e.type as string
  const base: FeedItem = { id: feedId(), kind: 'system', text: '', seq: e.seq }
  switch (type) {
    case 'host_line':
      return { ...base, kind: 'host', text: e.text ?? '' }
    case 'speech':
      return { ...base, kind: 'speech', seat: e.actor, text: e.text ?? '', name: e.name, avatar: e.avatar, related: [e.actor] }
    case 'last_words':
      return { ...base, kind: 'speech', seat: e.actor, text: e.text ?? '', tag: '遗言', related: [e.actor] }
    case 'wolf_proposal':
      return {
        ...base,
        kind: 'private',
        seat: e.actor,
        text: `提议刀 ${e.target}号${e.reason ? `：${e.reason}` : ''}`,
        tag: '狼队频道',
        related: [e.actor, e.target],
      }
    case 'wolf_target':
      return { ...base, kind: 'private', text: `今晚刀口定为 ${e.target}号`, tag: '狼队频道', related: [e.target] }
    case 'guard_protect':
      return { ...base, kind: 'private', seat: e.actor, text: `你今晚守护了 ${e.target}号`, tag: '守卫', related: [e.actor, e.target] }
    case 'seer_result':
      return {
        ...base,
        kind: 'private',
        seat: e.actor,
        text: `你查验了 ${e.target}号，结果是：${e.is_wolf ? '狼人' : '好人'}`,
        tag: '验人结果',
        related: [e.actor, e.target],
      }
    case 'witch_knife':
      return {
        ...base,
        kind: 'private',
        seat: e.actor,
        text: e.can_save ? `今晚 ${e.target}号 被袭击，是否使用解药？` : `今晚 ${e.target}号 被袭击（解药不可用）`,
        tag: '女巫',
        related: [e.actor, e.target],
      }
    case 'witch_save':
      return { ...base, kind: 'private', seat: e.actor, text: `你使用解药救下了 ${e.target}号`, tag: '女巫', related: [e.actor, e.target] }
    case 'witch_poison':
      return { ...base, kind: 'private', seat: e.actor, text: `你用毒药毒了 ${e.target}号`, tag: '女巫', related: [e.actor, e.target] }
    case 'night_settle':
      return { ...base, kind: 'night', text: e.text ?? '', related: (e.deaths ?? []).map(Number) }
    case 'hunter_shot':
      return { ...base, kind: 'hunter', seat: e.actor, text: `亮明猎人身份，开枪带走了 ${e.target}号！`, related: [e.actor, e.target] }
    case 'wolf_explode': {
      let text = `亮出狼人身份，自爆出局！白天结束，直接进入黑夜。`
      if (e.mode === 'single') text = `警上自爆！警长竞选终止，警徽本局流失，直接进入黑夜。`
      else if (e.mode === 'double_first') text = `警上自爆！警长竞选终止，明天将重新竞选警长。`
      else if (e.mode === 'double_second') text = `警上自爆！警徽被彻底吞掉，本局不再有警长。`
      return { ...base, kind: 'hunter', seat: e.actor, text, related: [e.actor] }
    }
    case 'sheriff_candidates': {
      const seats: number[] = e.seats ?? []
      return {
        ...base,
        kind: 'host',
        text: seats.length ? `上警竞选警长：${seats.map((s: number) => `${s}号`).join('、')}` : '无人上警，警徽作废。',
        related: seats,
      }
    }
    case 'sheriff_speech':
      return { ...base, kind: 'speech', seat: e.actor, text: e.text ?? '', tag: e.tag ?? '竞选宣言', related: [e.actor] }
    case 'sheriff_vote_cast':
      return {
        ...base,
        kind: 'voteLive',
        seat: e.seat,
        name: e.name,
        target: e.target,
        text: '',
        tag: '竞选投票',
        related: e.target ? [e.seat, e.target] : [e.seat],
      }
    case 'sheriff_result':
      return {
        ...base,
        kind: 'host',
        text: e.destroyed ? `警长竞选结束：${e.reason ?? '警徽作废'}，本局没有警长。` : `${e.sheriff}号 当选警长！投票计 1.5 票。`,
        related: e.destroyed ? [] : [e.sheriff],
      }
    case 'badge_pass':
      return {
        ...base,
        kind: 'host',
        text: e.destroyed ? `${e.from}号（警长）出局，撕毁了警徽，本局再无警长。` : `${e.from}号（警长）出局，警徽移交给 ${e.to}号`,
        related: [e.from, e.to],
      }
    case 'speech_order':
      return {
        ...base,
        kind: 'host',
        text: e.start ? `警长决定首日从 ${e.start}号 开始发言（警长最后发言）。` : '',
        related: e.start ? [e.start] : [],
      }
    case 'vote_cast':
      return {
        ...base,
        kind: 'voteLive',
        seat: e.seat,
        name: e.name,
        target: e.target,
        text: '',
        reason: e.reason ?? '',
        related: e.target ? [e.seat, e.target] : [e.seat],
      }
    case 'thinking':
      // 空思考（模型无 reasoning 输出）不落卡；玩家视角的固化思考文本被服务端剥离，同样跳过
      if (!e.text) return null
      return { ...base, kind: 'thinking', seat: e.seat, text: e.text, related: [e.seat] }
    case 'vote_result': {
      const votes: { seat: number; target: number; reason: string }[] = e.votes ?? []
      const lines = votes.map((v) => (v.target === 0 ? `${v.seat}号弃票` : `${v.seat}号 → ${v.target}号`)).join('，')
      const allAbstain = votes.length > 0 && votes.every((v) => v.target === 0)
      let result: string
      if (e.tie) result = allAbstain ? '全员弃票，无人出局。' : '平票，无人出局。'
      else if (e.exiled != null && e.idiot_survived)
        result = `${e.exiled}号翻牌是【${ROLE_LABELS[e.role as string] ?? e.role}】，免死并失去投票权，仍留在场上`
      else if (e.exiled != null)
        result = e.role ? `${e.exiled}号被放逐出局，翻牌是【${ROLE_LABELS[e.role as string] ?? e.role}】` : `${e.exiled}号被放逐出局`
      else result = ''
      const related = Array.from(new Set(votes.flatMap((v) => (v.target ? [v.seat, v.target] : [v.seat]))))
      if (e.exiled != null) related.push(e.exiled)
      return {
        ...base,
        kind: 'vote',
        text: result ? `${lines}\n${result}` : lines,
        related,
      }
    }
    case 'llm_paused':
      return { ...base, kind: 'system', text: `⏸ 对局已暂停：${e.message ?? '模型服务异常'}` }
    case 'game_over':
      return { ...base, kind: 'gameover', text: e.text ?? '' }
    default:
      return null
  }
}

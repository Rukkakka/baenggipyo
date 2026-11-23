export const meta = {
  name: 'web-parity-panel',
  description: '소스·범위마다 web-parity-investigator 하나를 동시에 돌려(같은 소스는 한 번에 하나씩) baenggipyo 명령이 그 사이트 웹과 어긋나는지(A 어긋남·B 의도한 차이·C 같음·D 웹에만 있음) 조사한다. 읽기 전용',
  whenToUse: '/web-parity 명령이 조사원이 둘 이상일 때(소스 둘 이상, 또는 한 소스를 범위로 나눔) 부른다. args.targets 에 [{source, scope}] 를 조사원 하나당 하나씩 넘긴다. args 없이 불렸으면 아무것도 띄우지 않고 /web-parity 를 쓰라고 안내한다.',
  phases: [
    { title: '조사', detail: '소스·범위마다 조사원 하나, 소스끼리는 동시에 (같은 소스는 하나씩)' },
  ],
}

const SOURCES = ['naver', 'skyscanner']
const EFFORTS = ['medium', 'high']  // 브라우저 조작이 든 조사라 모델은 Opus 만 쓴다. effort 는 medium~high (CLAUDE.md)
// 같은 소스에 위장 브라우저 여럿을 동시에 띄워 본 적이 없다. 늘리려면 실측한 뒤 docs/development/agents.md "동시에 띄울 때" 와
// 함께 고친다. 이 값은 웹 대조 조사원용이고 구현 작업자(`implement-bundles`)의 상한(셋)과 일부러 다르다.
// 워크플로 스크립트는 다른 파일을 불러올 수 없어 파일마다 상한을 둔다.
const SAME_SOURCE_MAX = 1  // 같은 소스를 동시에 조사하는 조사원 수 (docs/development/agents.md "동시에 띄울 때")

let a = args
// args 가 JSON 문자열로, 때로는 문자열이 한 번 더 감싸여 올 수 있어 객체가 될 때까지 푼다. 읽지 못하면 소스 없이 불린 것과
// 가르게 따로 알린다 (implement-bundles 와 같다)
// 빈 글자·공백뿐인 글자는 읽지 못한 것이 아니라 인자 없이 불린 것이라, 아래 '대상 없이 불림' 안내로 보낸다
if (typeof a === 'string' && !a.trim()) a = null
let parseError = null
for (let i = 0; i < 3 && typeof a === 'string'; i++) {
  try { a = JSON.parse(a) } catch (e) { parseError = String(e); a = null }
}
if (parseError) {
  log(`args 를 JSON 으로 읽지 못해 아무것도 띄우지 않았다: ${parseError}`)
  return {
    started: false,
    reason: 'bad-args',
    error: parseError,
    next: 'args 를 문자열로 감싸지 않고 객체(JSON 값)로 다시 넘긴다.',
  }
}
const targets = a && Array.isArray(a.targets) ? a.targets : []
if (!targets.length) {
  log('조사할 소스(targets) 없이 불렸다. 아무것도 띄우지 않았다')
  return { started: false, next: '이 워크플로는 /web-parity 명령이 소스와 범위를 정해 부른다. 사용자에게 /web-parity 를 쓰라고 알리고, 직접 다시 부르지 않는다.' }
}
const bad = targets.filter(t => !t || !SOURCES.includes(t.source) || typeof t.scope !== 'string' || !t.scope.trim())
// 같은 소스에 같은 범위를 두 번 넣으면 조사원 둘이 같은 것을 본다 (범위는 겹치지 않게 나눈다, docs/development/agents.md).
const keys = targets.filter(t => !bad.includes(t)).map(t => `${t.source}: ${t.scope.trim()}`)
const dup = [...new Set(keys.filter((k, i) => keys.indexOf(k) !== i))]
if (bad.length || dup.length) {
  log(`targets 가 잘못됐다 (모르는 소스·빈 범위 ${bad.length}건, 두 번 들어간 범위: ${dup.join(', ') || '없음'}). 아무것도 띄우지 않았다`)
  return {
    started: false,
    reason: 'bad-targets',
    next: `targets 는 [{source, scope}] 이고 source 는 ${SOURCES.join('·')} 중 하나, scope 는 빈 글이 아니다. 같은 소스를 범위별로 여럿 넣어도 되지만 같은 범위를 두 번 넣지 않는다 (명령이 겹치지 않게). 고쳐서 다시 부른다.`,
  }
}

// 같은 소스의 범위가 여럿이면 이름에 번호를 붙인다 (naver#1, naver#2).
const bySource = {}
targets.forEach((t, i) => (bySource[t.source] ||= []).push(i))
const nameOf = i => {
  const same = bySource[targets[i].source]
  return same.length > 1 ? `${targets[i].source}#${same.indexOf(i) + 1}` : targets[i].source
}

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          command: { type: 'string', description: '명령·옵션' },
          verdict: { type: 'string', enum: ['A', 'B', 'C', 'unconfirmed'] },
          web: { type: 'string', description: '웹 동작: API·파라미터·기본값과 캡처 근거' },
          baenggipyo: { type: 'string', description: 'baenggipyo 동작과 파일:줄' },
          reason: { type: 'string', description: '판정 이유 한두 문장' },
          documented: { type: 'boolean', description: 'B 일 때 그 소스 가이드 "웹과 다른 점" 에 이미 적혔는지' },
        },
        required: ['command', 'verdict', 'web', 'baenggipyo', 'reason'],
      },
    },
    webOnly: {
      type: 'array',
      description: 'D: 웹에는 있는데 baenggipyo 에 없는 기능 (로그인 없이 되는 것만)',
      items: { type: 'object', properties: { feature: { type: 'string' }, endpoint: { type: 'string' } }, required: ['feature', 'endpoint'] },
    },
    files: { type: 'array', items: { type: 'string' }, description: 'scratchpad 에 만든 캡처·작업 파일 경로' },
  },
  required: ['findings', 'webOnly', 'files'],
}

targets.forEach((t, i) => {
  if (t.model && t.model !== 'opus') log(`조사원 ${nameOf(i)}: 브라우저 조사라 ${t.model} 대신 opus 로 돌린다`)
  if (t.effort && !EFFORTS.includes(t.effort)) log(`조사원 ${nameOf(i)}: effort ${t.effort} 대신 high 로 돌린다`)
})

// agent() 가 예외를 던져도 그 조사원만 실패로 두고 이어 간다.
const tryAgent = async (prompt, opts) => {
  try { return await agent(prompt, opts) } catch (e) { log(`${opts.label}: 예외로 끝났다 (${String(e && e.message || e).slice(0, 120)})`); return null }
}

const investigate = i => {
  const t = targets[i]
  const others = bySource[t.source].filter(k => k !== i).map(k => targets[k].scope)
  return tryAgent(
    [
      `조사할 소스: ${t.source}`,
      `범위: ${t.scope}`,
      '',
      '저장소 파일은 고치지 않는다. 작업 파일은 scratchpad 에만 만든다.',
      `다른 조사원이 다른 소스를 동시에 조사하고 있을 수 있다. ${t.source} 사이트와 baenggipyo ${t.source} 명령만 부른다.`,
      ...(others.length ? [`${t.source} 의 다른 범위는 다른 조사원이 네 앞뒤로 차례로 본다 (같은 소스는 한 번에 하나라 동시에 돌지 않는다. baenggipyo 명령의 요청 제한은 함께 세지만 직접 여는 브라우저는 세지 않는다). 네 범위만 본다. 다른 범위: ${others.join(' / ')}`] : []),
      '판정 기준·방법·요청 한도는 네 정의를 따른다. A 는 캡처나 실제 조회로 근거를 대고, 확인하지 못한 것은 unconfirmed 로 둔다.',
      'B 는 그 소스 가이드 "웹과 다른 점" 에 이미 적혔는지 documented 로 적는다. C 는 범위 안에서 확인한 것만 짧게 적는다.',
    ].join('\n'),
    {
      label: `조사:${nameOf(i)}`,
      phase: '조사',
      agentType: 'web-parity-investigator',
      model: 'opus',
      effort: EFFORTS.includes(t.effort) ? t.effort : 'high',
      schema: FINDINGS,
    },
  )
}

phase('조사')
// 소스끼리는 동시에. 같은 소스는 동시에 SAME_SOURCE_MAX 명까지 돌리고 남은 범위는 그 뒤에 돌린다 (요청 수는
// baenggipyo 요청 제한기가 프로세스끼리 합쳐서 세지만, 조사원이 직접 여는 브라우저는 세지 않는다).
// 자리 SAME_SOURCE_MAX 개가 남은 범위를 하나씩 꺼내 간다. 여럿씩 잘라 돌리면 한 번에 띄운 조사원이 모두 끝나야 다음을 띄워,
// 먼저 끝난 자리가 가장 늦은 조사원을 기다린다
const reports = []
await parallel(Object.values(bySource).map(same => async () => {
  let next = 0
  const lane = async () => {
    while (next < same.length) {
      const i = same[next++]
      reports[i] = await investigate(i)
    }
  }
  await parallel(Array.from({ length: Math.min(SAME_SOURCE_MAX, same.length) }, () => lane))
}))

const results = targets.map((t, i) => ({ source: t.source, name: nameOf(i), scope: t.scope, ok: Boolean(reports[i]), ...(reports[i] || {}) }))
const failed = results.filter(r => !r.ok).map(r => r.name)
if (failed.length) log(`결과를 내지 못한 조사원: ${failed.join(', ')}`)
const count = v => results.reduce((n, r) => n + (r.findings || []).filter(f => f.verdict === v).length, 0)
log(`A ${count('A')}, B ${count('B')}, C ${count('C')}, 미확인 ${count('unconfirmed')}, D ${results.reduce((n, r) => n + (r.webOnly || []).length, 0)}`)
return { started: true, failed, results }

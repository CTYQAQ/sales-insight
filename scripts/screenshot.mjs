/**
 * 用 CDP 驱动本机 Edge/Chrome 截图，产出 README 用的演示图。
 *
 * 为什么不用 Playwright：为了一次截图装 500MB Chromium 不划算，
 * 本机已有 Edge，Node 22 又自带 WebSocket，直接说 CDP 就够了。
 *
 * 用法：
 *   1. 先启动后端(8000) 与前端(5173)
 *   2. node scripts/screenshot.mjs
 */
import { spawn } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import { setTimeout as sleep } from 'node:timers/promises'

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const PORT = 9222
const BASE = process.env.SHOT_BASE || 'http://127.0.0.1:5173'
const OUT = 'docs/screenshots'
const W = 1440
const H = 900

const profile = `${process.env.TEMP}/edge-shot-profile`

const browser = spawn(
  EDGE,
  [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    '--disable-gpu',
    '--hide-scrollbars',
    '--no-first-run',
    '--disable-extensions',
    `--window-size=${W},${H}`,
    'about:blank',
  ],
  { stdio: 'ignore', detached: false },
)

let id = 0
const pending = new Map()
let ws

function send(method, params = {}) {
  const mid = ++id
  ws.send(JSON.stringify({ id: mid, method, params }))
  return new Promise((resolve, reject) => {
    pending.set(mid, { resolve, reject })
    setTimeout(() => {
      if (pending.has(mid)) {
        pending.delete(mid)
        reject(new Error(`CDP timeout: ${method}`))
      }
    }, 30_000)
  })
}

async function waitForTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`)
      const list = await r.json()
      const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl)
      if (page) return page.webSocketDebuggerUrl
    } catch {
      /* 还没起来 */
    }
    await sleep(300)
  }
  throw new Error('等不到调试目标，Edge 可能没起来')
}

async function connect() {
  const url = await waitForTarget()
  ws = new WebSocket(url)
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true })
    ws.addEventListener('error', rej, { once: true })
  })
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    }
  })
  await send('Page.enable')
  await send('Runtime.enable')
  await send('Emulation.setDeviceMetricsOverride', {
    width: W,
    height: H,
    deviceScaleFactor: 2,
    mobile: false,
  })
}

async function evaluate(expression) {
  const r = await send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' ' + expression.slice(0, 80))
  return r.result?.value
}

async function goto(path) {
  await send('Page.navigate', { url: BASE + path })
  await sleep(1200)
}

async function shot(name) {
  // 先量真实内容高度，把视口撑到那么高再截 —— 否则长页面会被 900px 截断，
  // 而 captureBeyondViewport 对 ECharts 的 canvas 会截成空白。
  const h = await evaluate(
    `Math.max(document.body.scrollHeight, document.querySelector('#root')?.scrollHeight || 0)`,
  )
  const targetH = Math.min(Math.max(h || H, H), 2600)
  await send('Emulation.setDeviceMetricsOverride', {
    width: W,
    height: targetH,
    deviceScaleFactor: 2,
    mobile: false,
  })
  await sleep(900) // 等布局重排 + ECharts resize
  const { data } = await send('Page.captureScreenshot', { format: 'png' })
  await writeFile(`${OUT}/${name}.png`, Buffer.from(data, 'base64'))
  console.log('  ✓', `${name}.png`, `(${W}x${targetH})`)
  // 还原，避免影响下一张
  await send('Emulation.setDeviceMetricsOverride', {
    width: W,
    height: H,
    deviceScaleFactor: 2,
    mobile: false,
  })
}

/** 等某个选择器出现（antd 渲染有延迟，固定 sleep 不可靠） */
async function waitFor(selector, timeout = 15_000) {
  const start = Date.now()
  while (Date.now() - start < timeout) {
    const ok = await evaluate(`!!document.querySelector(${JSON.stringify(selector)})`)
    if (ok) return true
    await sleep(250)
  }
  return false
}

const main = async () => {
  await mkdir(OUT, { recursive: true })
  await connect()

  console.log('登录中…')
  // 注意：没有 /login 路由 —— 未登录时守卫组件直接在当前路由渲染登录页。
  // 导航到 /login 会命中 "No routes matched"，页面全空。
  await goto('/')
  await waitFor('input#username, input[placeholder="用户名"]')

  // React 受控输入必须走原生 setter + input 事件，直接赋 value 不会触发 onChange
  await evaluate(`
    (() => {
      const setVal = (el, v) => {
        const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
        d.set.call(el, v)
        el.dispatchEvent(new Event('input', { bubbles: true }))
      }
      const u = document.querySelector('input#username') || document.querySelector('input[placeholder="用户名"]')
      const p = document.querySelector('input#password') || document.querySelector('input[placeholder="密码"]')
      setVal(u, 'admin')
      setVal(p, 'admin123')
      return true
    })()
  `)
  await sleep(300)
  await evaluate(`document.querySelector('button[type="submit"]').click()`)
  await sleep(2500)

  // 登录后停在 "/"（看板）。等统计卡片 + ECharts 都出来再截，
  // 固定 sleep 容易截到"加载中"的空壳。
  const dashOk = await waitFor('canvas, .ant-statistic', 20_000)
  console.log('看板渲染:', dashOk)
  await sleep(3500) // 等 ECharts 入场动画结束
  await shot('01-dashboard')

  console.log('对话助手…')
  await goto('/chat')
  await waitFor('textarea, .ant-input')
  await evaluate(`
    (() => {
      const setVal = (el, v) => {
        const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v)
        el.dispatchEvent(new Event('input', { bubbles: true }))
      }
      const box = document.querySelector('textarea') || document.querySelector('input.ant-input')
      setVal(box, '华中区有没有江西')
      return true
    })()
  `)
  await sleep(400)
  await evaluate(`
    (() => {
      const btns = [...document.querySelectorAll('button')]
      const send = btns.find((b) => /发送|提问/.test(b.textContent || ''))
      if (send) { send.click(); return 'clicked' }
      return 'no-button'
    })()
  `)
  // 等真实 LLM 回答：轮询意图标签（查知识库/查数据/一般对话）出现为止，
  // 固定 sleep 在模型慢的时候会截到"正在检索"的加载态
  const answered = await (async () => {
    const start = Date.now()
    while (Date.now() - start < 45_000) {
      const ok = await evaluate(
        `!![...document.querySelectorAll('.ant-tag')].find(t => /查知识库|查数据|一般对话/.test(t.textContent || ''))`,
      )
      if (ok) return true
      await sleep(1000)
    }
    return false
  })()
  console.log('LLM 应答:', answered)
  await sleep(1200)
  await shot('02-chat-rag')

  console.log('知识库…')
  await goto('/knowledge')
  await waitFor('.ant-statistic, .ant-table')
  await sleep(1500)
  await shot('03-knowledge')

  console.log('数据接入…')
  await goto('/ingest')
  await sleep(2000)
  await shot('04-ingest')

  console.log('设置…')
  await goto('/settings')
  // 每次 goto 都是整页加载，会话要从 refresh cookie 恢复，期间页面是全局 Spin。
  // .page-title 在上一页还残留在 DOM 里，不可靠；等设置页特有的控件出现。
  const settingsOk = await waitFor('.app-sider .ant-select-selector, .app-sider + * .ant-select', 20_000)
  console.log('设置页渲染:', settingsOk)
  await sleep(1500)
  await shot('05-settings')

  ws.close()
  browser.kill()
}

main()
  .then(() => {
    console.log('完成')
    process.exit(0)
  })
  .catch(async (e) => {
    console.error('截图失败:', e.message)
    try {
      browser.kill()
    } catch {}
    process.exit(1)
  })

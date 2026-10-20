/** 打开某个路由，抓 console / 未捕获异常 / 最终 DOM 文本，定位白屏原因。 */
import { spawn } from 'node:child_process'
import { setTimeout as sleep } from 'node:timers/promises'

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const PORT = 9333
const ROUTE = process.argv[2] || '/ingest'
const BASE = 'http://127.0.0.1:5173'

const browser = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${process.env.TEMP}/edge-diag-profile`,
  '--disable-gpu', '--no-first-run', '--window-size=1440,900', 'about:blank',
], { stdio: 'ignore' })

let id = 0
const pending = new Map()
let ws

function send(method, params = {}) {
  const mid = ++id
  ws.send(JSON.stringify({ id: mid, method, params }))
  return new Promise((resolve, reject) => {
    pending.set(mid, { resolve, reject })
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); reject(new Error(`timeout ${method}`)) } }, 20000)
  })
}

async function target() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`)
      const list = await r.json()
      const page = list.find((t) => t.type === 'page')
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl
    } catch { /* not ready */ }
    await sleep(250)
  }
  throw new Error('no target')
}

const errors = []
const logs = []

const url = await target()
ws = new WebSocket(url)
await new Promise((res) => { ws.onopen = res })
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data)
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id).resolve(msg.result)
    pending.delete(msg.id)
  } else if (msg.method === 'Runtime.exceptionThrown') {
    const d = msg.params.exceptionDetails
    errors.push(`${d.text} ${d.exception?.description || ''}`.slice(0, 500))
  } else if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
    logs.push(msg.params.args.map((a) => String(a.value ?? a.description ?? '')).join(' ').slice(0, 300))
  }
}

await send('Runtime.enable')
await send('Page.enable')

// 先登录（没有 /login 路由，未登录时守卫在 "/" 渲染登录页）
await send('Page.navigate', { url: `${BASE}/` })
await sleep(2500)
await send('Runtime.evaluate', {
  expression: `
    (() => {
      const setVal = (el, v) => {
        const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
        d.set.call(el, v)
        el.dispatchEvent(new Event('input', { bubbles: true }))
      }
      const u = document.querySelector('input#username') || document.querySelector('input[placeholder="用户名"]')
      const p = document.querySelector('input#password') || document.querySelector('input[placeholder="密码"]')
      if (!u || !p) return 'no form'
      setVal(u, 'admin'); setVal(p, 'admin123')
      document.querySelector('button[type="submit"]').click()
      return 'submitted'
    })()
  `,
  returnByValue: true,
})
await sleep(2500)

errors.length = 0
logs.length = 0

await send('Page.navigate', { url: `${BASE}${ROUTE}` })
await sleep(4500)

const text = await send('Runtime.evaluate', {
  expression: 'document.body.innerText.slice(0, 400)',
  returnByValue: true,
})
const html = await send('Runtime.evaluate', {
  expression: 'document.getElementById("root")?.innerHTML.slice(0,200) || "NO ROOT"',
  returnByValue: true,
})

console.log('=== route ===', ROUTE)
console.log('=== exceptions ===')
errors.forEach((e) => console.log(e))
console.log('=== console.error ===')
logs.forEach((e) => console.log(e))
console.log('=== body text ===')
console.log(text.result.value)
console.log('=== root html ===')
console.log(html.result.value)

ws.close()
browser.kill()

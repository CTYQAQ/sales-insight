import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp, Button, Descriptions, Input, InputNumber, Popconfirm,
  Segmented, Select, Switch, Typography,
} from 'antd'
import { settingsApi, kbApi, type SettingsSnapshot } from '../api/api'
import { errMsg } from '../api/client'

/** 看板偏好存 localStorage：纯前端偏好，不值得为它建表 */
export const PREFS_KEY_DAYS = 'si.pref.defaultDays'
export const PREFS_KEY_UNIT = 'si.pref.moneyUnit'
export const PREFS_EVENT = 'si-prefs-changed'

export function readPrefDays(): number {
  const v = Number(localStorage.getItem(PREFS_KEY_DAYS))
  return [7, 30, 90].includes(v) ? v : 30
}
export function readPrefUnit(): 'auto' | 'wan' {
  return localStorage.getItem(PREFS_KEY_UNIT) === 'wan' ? 'wan' : 'auto'
}

/** 一个分区 = 左标签列（标题 + 说明）+ 右白面板，分区之间一根发丝线 */
function Section({
  title, desc, children, first = false,
}: {
  title: string; desc: string; children: React.ReactNode; first?: boolean
}) {
  return (
    <div
      style={{
        display: 'grid', gridTemplateColumns: '200px minmax(0, 1fr)', gap: 24,
        padding: '28px 0',
        borderTop: first ? 'none' : '1px solid var(--rule)',
      }}
    >
      <div>
        <div style={{ fontSize: 14, fontWeight: 500, marginTop: 4 }}>{title}</div>
        <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.7, marginTop: 4 }}>{desc}</div>
      </div>
      <div
        style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-card)', padding: 20, maxWidth: 760,
        }}
      >
        {children}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
      <span style={{ fontSize: 13, width: 104, flexShrink: 0 }}>{label}</span>
      {children}
    </div>
  )
}

function PanelFoot({ saving, onSave, extra }: {
  saving: boolean; onSave: () => void; extra?: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
      {extra}
      <Button type="primary" size="small" loading={saving} onClick={onSave}>保存</Button>
    </div>
  )
}

export default function Settings() {
  const { message } = AntApp.useApp()
  const [snap, setSnap] = useState<SettingsSnapshot | null>(null)
  const [saving, setSaving] = useState<string>('')
  const [testState, setTestState] = useState<{ testing: boolean; result?: string; ok?: boolean }>({ testing: false })
  const [notifyTest, setNotifyTest] = useState<string>('')

  // AI 模型表单：每家厂商一份 {key, base, model}，key 留空 = 不改动现有密钥
  const [provider, setProvider] = useState('deepseek')
  const [confs, setConfs] = useState<Record<string, { key: string; base: string; model: string }>>({})
  // 检索与 Embedding
  const [hybrid, setHybrid] = useState(true)
  const [embBackend, setEmbBackend] = useState('local')
  const [embModel, setEmbModel] = useState('')
  const [embDim, setEmbDim] = useState(512)
  const [embBase, setEmbBase] = useState('')
  const [embKey, setEmbKey] = useState('')
  // 通知
  const [whType, setWhType] = useState('none')
  const [whUrl, setWhUrl] = useState('')
  const [whKeyword, setWhKeyword] = useState('')
  const [smtpHost, setSmtpHost] = useState('')
  const [smtpPort, setSmtpPort] = useState(465)
  const [smtpUser, setSmtpUser] = useState('')
  const [smtpPass, setSmtpPass] = useState('')
  const [emailTo, setEmailTo] = useState('')
  // 看板偏好
  const [prefDays, setPrefDays] = useState(readPrefDays())
  const [prefUnit, setPrefUnit] = useState(readPrefUnit())
  const [prefSaved, setPrefSaved] = useState(false)

  const load = useCallback(async () => {
    try {
      const s = await settingsApi.get()
      setSnap(s)
      setProvider(s.llm.provider)
      setConfs(Object.fromEntries(
        Object.entries(s.llm.providers).map(([name, c]) => [name, { key: '', base: c.base_url, model: c.model }]),
      ))
      setHybrid(s.rag.hybrid)
      setEmbBackend(s.rag.embedding_backend); setEmbModel(s.rag.embedding_model)
      setEmbDim(s.rag.embedding_dim); setEmbBase(s.rag.embedding_base_url)
      setWhType(s.notify.webhook_type); setWhKeyword(s.notify.webhook_keyword)
      setSmtpHost(s.notify.smtp_host); setSmtpPort(s.notify.smtp_port)
      setSmtpUser(s.notify.smtp_user); setEmailTo(s.notify.email_to)
    } catch (e) {
      message.error(errMsg(e))
    }
  }, [message])

  useEffect(() => { void load() }, [load])

  const patch = async (tag: string, payload: Record<string, unknown>, okText: string) => {
    setSaving(tag)
    try {
      const r = await settingsApi.patch(payload)
      if (r.needs_rebuild) {
        message.warning('已保存。Embedding 配置变了，请到「知识库」页重建索引后再检索')
      } else {
        message.success(okText)
      }
      setConfs((prev) => Object.fromEntries(Object.entries(prev).map(([k, v]) => [k, { ...v, key: '' }])))
      setEmbKey(''); setSmtpPass('')
      await load()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setSaving('')
    }
  }

  const saveLlm = () => {
    const prefix = provider.toUpperCase()
    const conf = confs[provider]
    return patch('llm', {
      LLM_PROVIDER: provider,
      [`${prefix}_BASE_URL`]: conf?.base ?? '',
      [`${prefix}_MODEL`]: conf?.model ?? '',
      // 密钥留空 = 不动；填了才覆盖。后端对 None 与 "" 区分处理
      ...(conf?.key ? { [`${prefix}_API_KEY`]: conf.key } : {}),
    }, '模型配置已保存，立即生效')
  }

  const saveRag = () => patch('rag', {
    RAG_HYBRID: hybrid,
    EMBEDDING_BACKEND: embBackend,
    EMBEDDING_MODEL: embModel,
    EMBEDDING_DIM: embDim,
    EMBEDDING_BASE_URL: embBase,
    ...(embKey ? { EMBEDDING_API_KEY: embKey } : {}),
  }, '检索配置已保存')

  const saveNotify = () => patch('notify', {
    WEBHOOK_TYPE: whType,
    WEBHOOK_KEYWORD: whKeyword,
    ...(whUrl ? { WEBHOOK_URL: whUrl } : {}),
    SMTP_HOST: smtpHost,
    SMTP_PORT: smtpPort,
    SMTP_USER: smtpUser,
    ALERT_EMAIL_TO: emailTo,
    ...(smtpPass ? { SMTP_PASSWORD: smtpPass } : {}),
  }, '通知配置已保存')

  const runLlmTest = async () => {
    setTestState({ testing: true })
    try {
      const r = await settingsApi.testLlm()
      setTestState({ testing: false, ok: true, result: `${r.provider} / ${r.model} · ${r.elapsed_ms} ms · 回复"${r.reply}"` })
    } catch (e) {
      setTestState({ testing: false, ok: false, result: errMsg(e) })
    }
  }

  const runNotifyTest = async (channel: 'webhook' | 'email') => {
    setNotifyTest(channel)
    try {
      await settingsApi.testNotify(channel)
      message.success(channel === 'webhook' ? '测试消息已推送到 Webhook' : '测试邮件已发出')
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setNotifyTest('')
    }
  }

  const savePrefs = () => {
    localStorage.setItem(PREFS_KEY_DAYS, String(prefDays))
    localStorage.setItem(PREFS_KEY_UNIT, prefUnit)
    window.dispatchEvent(new Event(PREFS_EVENT))
    setPrefSaved(true)
    setTimeout(() => setPrefSaved(false), 2000)
  }

  return (
    <div>
      <h1 className="page-title">设置</h1>
      <p className="page-desc">模型、检索、通知与看板默认值；保存后立即生效并写回 .env，重启不丢</p>

      {/* ---------------- AI 模型 ---------------- */}
      <Section first title="AI 模型" desc="对话、问数、报告共用的一组模型。改完立刻生效，不用重启。">
        <Field label="对话模型">
          <Select
            value={provider}
            onChange={setProvider}
            style={{ width: 240 }}
            options={Object.entries(snap?.llm.providers ?? {}).map(([name, c]) => ({
              value: name,
              label: `${c.label}${c.api_key_set ? '' : c.needs_key ? '（未配置 Key）' : ''}`,
            }))}
          />
        </Field>
        {(() => {
          const vendor = snap?.llm.providers[provider]
          const conf = confs[provider]
          if (!vendor || !conf) return null
          const setConf = (k: 'key' | 'base' | 'model', v: string) =>
            setConfs((prev) => ({ ...prev, [provider]: { ...prev[provider], [k]: v } }))
          return (
            <>
              {vendor.needs_key && (
                <Field label="API Key">
                  <Input.Password
                    style={{ maxWidth: 320 }}
                    placeholder={vendor.api_key_set ? `当前：${vendor.api_key_masked}（留空保持不变）` : `未配置 ${provider.toUpperCase()}_API_KEY`}
                    value={conf.key}
                    onChange={(e) => setConf('key', e.target.value)}
                  />
                </Field>
              )}
              <Field label="接口地址">
                <Input style={{ maxWidth: 380 }} value={conf.base} onChange={(e) => setConf('base', e.target.value)} />
              </Field>
              <Field label="模型">
                <Input style={{ maxWidth: 320 }} value={conf.model} onChange={(e) => setConf('model', e.target.value)} />
              </Field>
            </>
          )
        })()}
        {testState.result && (
          <Typography.Text
            style={{ fontSize: 12, display: 'block', marginBottom: 8, color: testState.ok ? 'var(--down)' : 'var(--up)' }}
          >
            {testState.ok ? '连通正常：' : '连接失败：'}{testState.result}
          </Typography.Text>
        )}
        <PanelFoot saving={saving === 'llm'} onSave={() => void saveLlm()}
          extra={<Button size="small" loading={testState.testing} onClick={() => void runLlmTest()}>测试连接</Button>} />
      </Section>

      {/* ---------------- 检索与 Embedding ---------------- */}
      <Section title="检索与 Embedding" desc="混合检索随时可切；换 Embedding 模型等于换了一个向量空间，旧索引作废，必须重建。">
        <Field label="混合检索">
          <Switch checked={hybrid} onChange={setHybrid} />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {hybrid ? 'BM25 + 向量融合（推荐）' : '纯向量：带精确实体的问题容易跑偏'}
          </Typography.Text>
        </Field>
        <Field label="Embedding">
          <Select
            value={embBackend} onChange={setEmbBackend} style={{ width: 220 }}
            options={[
              { value: 'local', label: 'local · 内置哈希（零依赖演示）' },
              { value: 'bge', label: 'bge · 本地 BGE 模型（推荐）' },
              { value: 'api', label: 'api · OpenAI 兼容接口' },
            ]}
          />
        </Field>
        {embBackend !== 'local' && (
          <>
            <Field label="模型名">
              <Input style={{ maxWidth: 320 }} value={embModel} onChange={(e) => setEmbModel(e.target.value)} />
            </Field>
            <Field label="向量维度">
              <InputNumber min={64} max={4096} value={embDim} onChange={(v) => setEmbDim(Number(v))} />
            </Field>
          </>
        )}
        {embBackend === 'api' && (
          <>
            <Field label="接口地址">
              <Input style={{ maxWidth: 320 }} value={embBase} onChange={(e) => setEmbBase(e.target.value)} />
            </Field>
            <Field label="API Key">
              <Input.Password
                style={{ maxWidth: 320 }}
                placeholder={snap?.rag.embedding_api_key_set ? '已配置（留空保持不变）' : '未配置'}
                value={embKey}
                onChange={(e) => setEmbKey(e.target.value)}
              />
            </Field>
          </>
        )}
        <PanelFoot saving={saving === 'rag'} onSave={() => void saveRag()} />
      </Section>

      {/* ---------------- 告警通知 ---------------- */}
      <Section title="告警通知" desc="规则触发后推送到这里。右侧预览就是你配置关键字后对方收到的样子。">
        <Field label="机器人">
          <Select
            value={whType} onChange={setWhType} style={{ width: 160 }}
            options={[
              { value: 'none', label: '不启用' },
              { value: 'dingtalk', label: '钉钉' },
              { value: 'wecom', label: '企业微信' },
              { value: 'feishu', label: '飞书' },
            ]}
          />
        </Field>
        {whType !== 'none' && (
          <>
            <Field label="Webhook">
              <Input
                style={{ maxWidth: 380 }}
                placeholder={snap?.notify.webhook_url_set ? `当前：${snap.notify.webhook_url_masked}（留空保持不变）` : '粘贴机器人 Webhook 地址'}
                value={whUrl}
                onChange={(e) => setWhUrl(e.target.value)}
              />
            </Field>
            <Field label="安全关键字">
              <Input style={{ maxWidth: 200 }} value={whKeyword} onChange={(e) => setWhKeyword(e.target.value)} />
            </Field>
            <Field label="">
              <Button size="small" loading={notifyTest === 'webhook'} onClick={() => void runNotifyTest('webhook')}>
                发送测试消息
              </Button>
            </Field>
          </>
        )}
        <div style={{ borderTop: '1px solid var(--rule)', margin: '8px 0 16px' }} />
        <Field label="SMTP 主机">
          <Input style={{ maxWidth: 220 }} placeholder="如 smtp.qq.com" value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} />
          <InputNumber min={1} max={65535} value={smtpPort} onChange={(v) => setSmtpPort(Number(v))} />
        </Field>
        <Field label="邮箱账号">
          <Input style={{ maxWidth: 220 }} value={smtpUser} onChange={(e) => setSmtpUser(e.target.value)} />
          <Input.Password
            style={{ maxWidth: 180 }}
            placeholder={snap?.notify.smtp_password_set ? '授权码已配置（留空不变）' : '授权码'}
            value={smtpPass}
            onChange={(e) => setSmtpPass(e.target.value)}
          />
        </Field>
        <Field label="收件人">
          <Input style={{ maxWidth: 320 }} placeholder="多个用英文逗号分隔" value={emailTo} onChange={(e) => setEmailTo(e.target.value)} />
        </Field>
        <Field label="">
          <Button size="small" loading={notifyTest === 'email'} onClick={() => void runNotifyTest('email')}>发送测试邮件</Button>
        </Field>

        <div style={{ background: '#f9fafb', borderRadius: 6, padding: '10px 14px', margin: '4px 0 14px' }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>推送预览</div>
          <div style={{ fontSize: 13, lineHeight: 1.7 }}>
            {whKeyword ? `${whKeyword}告警\n` : ''}【严重】华东区销售额环比下降 34.2%（阈值 30%）
            <br />
            <span style={{ color: 'var(--muted)', fontSize: 12 }}>实际 ¥182.4 万 · 对比周期 ¥277.3 万 · 2026-10-20 06:30</span>
          </div>
        </div>
        <PanelFoot saving={saving === 'notify'} onSave={() => void saveNotify()} />
      </Section>

      {/* ---------------- 看板偏好（本机） ---------------- */}
      <Section title="看板偏好" desc="只存在这台机器的浏览器里，不影响其他用户，也不会写进 .env。">
        <Field label="默认时间窗">
          <Segmented
            value={prefDays}
            onChange={(v) => setPrefDays(Number(v))}
            options={[{ label: '近 7 天', value: 7 }, { label: '近 30 天', value: 30 }, { label: '近 90 天', value: 90 }]}
          />
        </Field>
        <Field label="金额单位">
          <Segmented
            value={prefUnit}
            onChange={(v) => setPrefUnit(v as 'auto' | 'wan')}
            options={[{ label: '自动（元 / 万 / 亿）', value: 'auto' }, { label: '一律用万元', value: 'wan' }]}
          />
        </Field>
        <PanelFoot saving={false} onSave={savePrefs}
          extra={prefSaved ? <Typography.Text style={{ fontSize: 12, color: 'var(--down)', alignSelf: 'center' }}>已保存</Typography.Text> : undefined} />
      </Section>

      {/* ---------------- 系统信息（只读） ---------------- */}
      <Section title="系统信息" desc="当前部署的实情，排障时先看这里。">
        {snap && (
          <Descriptions size="small" column={2} colon={false}
            items={[
              { key: 'v', label: '版本', children: `${snap.system.app_name} 0.1.0（${snap.system.app_env}）` },
              { key: 'db', label: '业务库', children: snap.system.database },
              { key: 'mv', label: '向量库', children: `${snap.system.milvus_mode} · ${snap.system.milvus_conn}` },
              { key: 'em', label: 'Embedding', children: `${snap.rag.embedding_backend} · ${snap.rag.embedding_model} · ${snap.rag.embedding_dim} 维` },
              { key: 't2s', label: 'Text2SQL', children: snap.system.text2sql_enabled ? '已启用（白名单校验）' : '已停用' },
            ]}
          />
        )}
      </Section>

      {/* ---------------- 危险区（最后，写明后果，确认要点名对象） ---------------- */}
      <Section title="危险区" desc="这里的操作会清空数据重来，只在演示环境使用。">
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Popconfirm
            title="重建演示数据？"
            description="会清空 fact_sales 等全部业务表并重新生成 1,200 行演示数据，已上传的真实数据也会丢失。"
            okText="重建" cancelText="取消" okButtonProps={{ danger: true }}
            onConfirm={async () => {
              try {
                const r = await settingsApi.demoReset()
                message.success(`已重建演示数据：${r.rows_written.toLocaleString()} 行`)
              } catch (e) { message.error(errMsg(e)) }
            }}
          >
            <Button danger size="small">重建演示数据</Button>
          </Popconfirm>
          <Popconfirm
            title="清空知识库？"
            description="会删除所有已入库片段与已上传文档，需要在「知识库」页重新灌入。"
            okText="清空" cancelText="取消" okButtonProps={{ danger: true }}
            onConfirm={async () => {
              try {
                await kbApi.reset()
                message.success('知识库已清空')
              } catch (e) { message.error(errMsg(e)) }
            }}
          >
            <Button danger size="small">清空知识库</Button>
          </Popconfirm>
        </div>
      </Section>
    </div>
  )
}

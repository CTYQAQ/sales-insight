import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Layout, Menu, Spin, Tag, Typography } from 'antd'
import {
  BellOutlined, BookOutlined, DashboardOutlined, DatabaseOutlined, FileTextOutlined,
  MessageOutlined, TeamOutlined,
} from '@ant-design/icons'
import { useAuth } from './store/AuthContext'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import DataIngest from './pages/DataIngest'
import Reports from './pages/Reports'
import Alerts from './pages/Alerts'
import Chat from './pages/Chat'
import Knowledge from './pages/Knowledge'
import Users from './pages/Users'

const { Sider, Content } = Layout

const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  analyst: '分析师',
  viewer: '查看者',
}

/** 产品标记：三根账柱，最后一根用唯一的数据 hue —— hue 只出现在数据相关处 */
function BrandMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
      <rect x="2" y="9" width="4" height="9" rx="1" fill="#141414" />
      <rect x="9" y="5" width="4" height="13" rx="1" fill="#141414" />
      <rect x="16" y="2" width="4" height="16" rx="1" fill="#1d5fbf" />
    </svg>
  )
}

function Shell() {
  const { user, logout } = useAuth()
  const loc = useLocation()
  const navigate = useNavigate()

  const items = [
    { key: '/', icon: <DashboardOutlined />, label: '销售看板' },
    { key: '/ingest', icon: <DatabaseOutlined />, label: '数据接入' },
    { key: '/reports', icon: <FileTextOutlined />, label: 'AI 报告' },
    { key: '/alerts', icon: <BellOutlined />, label: '异常告警' },
    { key: '/chat', icon: <MessageOutlined />, label: '对话助手' },
    { key: '/knowledge', icon: <BookOutlined />, label: '知识库' },
    ...(user?.role === 'admin'
      ? [{ key: '/users', icon: <TeamOutlined />, label: '用户管理' }]
      : []),
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* open plane 壳：侧栏与页面同底色，无顶栏；用户信息收到侧栏底部 */}
      <Sider
        className="app-sider"
        width={232}
        breakpoint="lg"
        collapsedWidth={0}
        style={{ display: 'flex', flexDirection: 'column' }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <div className="app-brand">
            <BrandMark />
            <div>
              <div className="app-brand-name">销售智能分析</div>
              <div className="app-brand-sub">Sales Insight</div>
            </div>
          </div>
          {/* 关键：走客户端路由。以前这里写的是 window.location.href = key，
              每次点菜单都是一次整页刷新，内存里的 access token 当场归零，
              于是表现为"点哪都要重新登录"。 */}
          <Menu
            mode="inline"
            selectedKeys={[loc.pathname]}
            items={items}
            onClick={({ key }) => navigate(key)}
            style={{ flex: 1 }}
          />
          <div className="app-sider-foot">
            <span className="user-line">
              {user?.username}
              <Tag style={{ marginInlineEnd: 0 }}>{ROLE_LABEL[user?.role ?? 'viewer']}</Tag>
            </span>
            <span>
              {user?.region_scope?.length
                ? `数据范围：${user.region_scope.join(' / ')}`
                : '数据范围：全部区域'}
            </span>
            <a onClick={logout}>退出登录</a>
          </div>
        </div>
      </Sider>
      <Content style={{ padding: '28px 32px', maxWidth: 1400 }}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/ingest" element={<DataIngest />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/users" element={user?.role === 'admin' ? <Users /> : <Navigate to="/" />} />
        </Routes>
      </Content>
    </Layout>
  )
}

export default function App() {
  const { user, loading } = useAuth()

  // 会话恢复完成前先挡住：否则子路由组件会抢在 access token 就绪前发请求，
  // 拿到 401 再被判定为未登录，白闪一次登录页。
  if (loading) {
    return (
      <div
        style={{
          display: 'flex', flexDirection: 'column', gap: 12,
          alignItems: 'center', justifyContent: 'center', minHeight: '100vh',
        }}
      >
        <Spin size="large" />
        <Typography.Text type="secondary">正在恢复会话…</Typography.Text>
      </div>
    )
  }

  return user ? <Shell /> : <Login />
}

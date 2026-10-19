import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider, App as AntApp } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import { AuthProvider } from './store/AuthContext'
// 自托管 Geist（npm @fontsource），不走 Google Fonts：国内网络下 Fonts CDN 经常超时，
// 会让首屏卡在字体加载上。Geist 只覆盖西文与数字（表格数字用它的 tabular figures），
// 中文按 font-family 顺序落到 PingFang / 微软雅黑。
import '@fontsource/geist/400.css'
import '@fontsource/geist/500.css'
import '@fontsource/geist/700.css'
import '@fontsource/geist-mono/400.css'
import '@fontsource/geist-mono/500.css'
import './index.css'

/**
 * 设计基调（achromatic-ledger）：
 * chrome 不上任何品牌色 —— 主按钮近黑、导航选中是中性灰 pill；
 * 唯一的 hue（#1d5fbf）只给主数据元素（趋势折线、排名条形）；
 * 红/绿只出现在状态与涨跌信号上（国内习惯：涨红跌绿）。
 */
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#141414',
          // info 提示条用数据 hue 的浅色调：antd 会拿 colorInfo 算 Alert 底色，
          // 给近黑会得到一条又黑又重的横幅
          colorInfo: '#1d5fbf',
          colorLink: '#0a0a0a',
          colorText: '#0a0a0a',
          colorTextSecondary: '#6b6b6b',
          colorBgLayout: '#f2f4f7',
          colorBorder: '#e6e6e6',
          colorBorderSecondary: '#f0f0f0',
          borderRadius: 8,
          controlHeight: 36,
          fontFamily:
            '"Geist", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Menu: {
            // 选中态用中性灰 pill，不用品牌色填充 —— 色彩预算只给数据
            itemBg: 'transparent',
            itemColor: '#5f6368',
            itemSelectedBg: '#e9ebee',
            itemSelectedColor: '#0a0a0a',
            itemHoverBg: '#eef0f3',
            itemBorderRadius: 8,
            itemMarginInline: 12,
            activeBarBorderWidth: 0,
          },
          Table: {
            // 表头：无底纹、12px 灰、句首大写；分隔靠行线不靠色块
            headerBg: 'transparent',
            headerColor: '#6b6b6b',
            headerSplitColor: 'transparent',
            rowHoverBg: '#f6f7f9',
            cellPaddingBlockSM: 10,
          },
          Card: {
            boxShadowTertiary: 'none',
            paddingLG: 20,
          },
          Button: {
            primaryShadow: 'none',
            defaultShadow: 'none',
          },
          Statistic: {
            titleFontSize: 13,
            contentFontSize: 24,
          },
        },
      }}
    >
      <AntApp>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
)

import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/drilldown'
const columns = [
  "路径编号",
  "起始维度",
  "下钻层级序列",
  "每层聚合粒度",
  "默认度量",
  "路径深度",
  "缓存秒数",
  "数据新鲜度"
]
const actions = [
  "提交路径评审",
  "启用路径",
  "归档路径"
]
const statuses = [
  "草稿",
  "路径评审中",
  "已启用",
  "已归档"
]
const stats = [
  {
    "label": "下钻路径总数",
    "value": 0
  },
  {
    "label": "已启用路径",
    "value": 0
  },
  {
    "label": "评审中路径",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type LevelCheck = { 层级: number; 维度: string; 状态: string; 说明: string }
type GapResult = {
  路径编号: string
  快照编号: string
  预设编号: string
  调用链: string[]
  层级检查: LevelCheck[]
  数据缺口: LevelCheck[]
}

export default function DrilldownView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [gapResult, setGapResult] = useState<GapResult | null>(null)
  const [gapMessage, setGapMessage] = useState('')
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('下钻路径列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '多维下钻列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('多维下钻动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '多维下钻操作失败')
    }
  }

  async function checkGaps(row: Row) {
    setErrorMessage('')
    setGapResult(null)
    setGapMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/snapshot-gaps`)
      const payload = await response.json()
      if (!response.ok) {
        setErrorMessage(payload.detail ?? '数据缺口检查失败')
        return
      }
      setGapResult(payload as GapResult)
      setGapMessage(payload.message ?? '')
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据缺口检查失败')
    }
  }

  return (
    <section className="page" data-module="drilldown">
      <header className="page-head">
        <div>
          <h2>多维下钻管理</h2>
          <p className="page-desc">定义多维度数据下钻的分析路径，从高层汇总到明细的逐层探索。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('下钻路径登记入口尚未接入审批流')}>
            登记下钻路径
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出多维下钻清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void checkGaps(row)}>
                  缺口检查
                </button>
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无多维下钻数据，可先登记下钻路径</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条多维下钻记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {gapResult && (
        <div className="panel gap-result">
          <h3>数据缺口检查：{gapResult.路径编号}</h3>
          <p className="panel-desc">
            消费快照 {gapResult.快照编号}（预设 {gapResult.预设编号}）；调用链：{gapResult.调用链.join(' → ')}。{gapMessage}
          </p>
          <table className="sub-table">
            <thead>
              <tr><th>层级</th><th>维度</th><th>状态</th><th>说明</th></tr>
            </thead>
            <tbody>
              {gapResult.层级检查.map((check) => (
                <tr key={`${gapResult.路径编号}-${check.层级}`}>
                  <td>{check.层级}</td>
                  <td>{check.维度}</td>
                  <td>
                    <span className={`tag ${check.状态 === '已覆盖' ? 'ok' : check.状态 === '失效' ? 'danger' : 'warn'}`}>
                      {check.状态}
                    </span>
                  </td>
                  <td>{check.说明}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {gapResult.数据缺口.length > 0 && (
            <p className="panel-desc">
              数据缺口：
              {gapResult.数据缺口.map((gap) => (
                <span key={`gap-${gap.层级}`} className="tag danger">第{gap.层级}层 {gap.维度}（{gap.状态}）</span>
              ))}
            </p>
          )}
        </div>
      )}
    </section>
  )
}

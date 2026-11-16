import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/filterpreset'
const SNAPSHOT_ENDPOINT = '/api/filtersnapshot'
const columns = [
  "预设编号",
  "预设名称",
  "维度条件组",
  "时间范围",
  "排序规则",
  "显示列",
  "共享状态",
  "使用次数"
]
const actions = [
  "提交预设评审",
  "发布预设",
  "废弃预设"
]
const STAGES = ["读取预设", "解析条件", "维度映射", "冲突裁决", "生成快照"]
const snapshotStatuses = ["生成中", "已生成", "已归档"]

type Row = Record<string, string | number | null>

type Condition = { 维度: string; 操作符: string; 值: string; 来源?: string; 映射路径?: string }
type InvalidDim = { 维度名: string; 原因: string; 来源?: string; 条件?: string }
type InvalidColumn = { 列名: string; 原因: string }
type Decision = { 维度: string; 操作符: string; 预设值: string; 请求值: string; 裁决: string; 说明: string }
type Snapshot = {
  id: number
  快照编号: string
  预设编号: string
  预设名称: string
  消费方: string
  status: string
  当前阶段: string
  已完成阶段: string[]
  使用次数: number
  生效条件: Condition[]
  失效维度: InvalidDim[]
  失效显示列: InvalidColumn[]
  冲突裁决记录: Decision[]
  套用查询: { statement?: string }
  时间范围: string
  创建时间: string
  完成时间: string
}
type InvalidReportRow = { 维度名: string; 出现次数: number; 涉及预设: string[]; 涉及快照: string[]; 最近出现时间: string; 失效原因: string }

export default function FilterpresetView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  // 套用预设
  const [applyTarget, setApplyTarget] = useState<Row | null>(null)
  const [applyForm, setApplyForm] = useState({ 显式条件: '', 消费方: '多维下钻', 演练中断阶段: '' })
  const [applyMessage, setApplyMessage] = useState('')

  // 筛选条件快照
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [snapTotal, setSnapTotal] = useState(0)
  const [snapFilters, setSnapFilters] = useState({ keyword: '', status: '', invalid_dimension: '', has_invalid: false })
  const [expandedId, setExpandedId] = useState<number | null>(null)

  // 失效维度定位报告
  const [invalidReport, setInvalidReport] = useState<InvalidReportRow[]>([])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('预设模板列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '筛选预设列表读取失败')
    }
  }, [filters])

  const reloadSnapshots = useCallback(async () => {
    const query = new URLSearchParams()
    if (snapFilters.keyword) query.set('keyword', snapFilters.keyword)
    if (snapFilters.status) query.set('status', snapFilters.status)
    if (snapFilters.invalid_dimension) query.set('invalid_dimension', snapFilters.invalid_dimension)
    if (snapFilters.has_invalid) query.set('has_invalid', 'true')
    try {
      const response = await request(`${SNAPSHOT_ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('筛选条件快照读取失败')
      }
      const payload = await response.json()
      setSnapshots(payload.items ?? [])
      setSnapTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '筛选条件快照读取失败')
    }
  }, [snapFilters])

  const reloadInvalidReport = useCallback(async () => {
    try {
      const response = await request(`${SNAPSHOT_ENDPOINT}/invalid-dimensions`)
      if (!response.ok) {
        throw new Error('失效维度报告读取失败')
      }
      const payload = await response.json()
      setInvalidReport(payload.items ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '失效维度报告读取失败')
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    void reloadSnapshots()
  }, [reloadSnapshots])

  useEffect(() => {
    void reloadInvalidReport()
  }, [reloadInvalidReport])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('筛选预设动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '筛选预设操作失败')
    }
  }

  async function applyPreset() {
    if (!applyTarget) return
    setApplyMessage('')
    setErrorMessage('')
    try {
      const values: Record<string, string> = { 消费方: applyForm.消费方 }
      if (applyForm.显式条件.trim()) values['显式条件'] = applyForm.显式条件
      if (applyForm.演练中断阶段) values['演练中断阶段'] = applyForm.演练中断阶段
      const response = await request(`${ENDPOINT}/${applyTarget.id}/apply`, {
        method: 'POST',
        body: JSON.stringify({ values }),
      })
      const payload = await response.json()
      setApplyMessage(payload.message ?? '套用完成')
      setApplyTarget(null)
      await Promise.all([reload(), reloadSnapshots(), reloadInvalidReport()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '预设套用失败')
    }
  }

  async function runSnapshotAction(action: string, snap: Snapshot) {
    setErrorMessage('')
    try {
      const response = await request(`${SNAPSHOT_ENDPOINT}/${snap.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message ?? '快照动作未生效')
        return
      }
      await Promise.all([reload(), reloadSnapshots(), reloadInvalidReport()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '快照操作失败')
    }
  }

  function locateInvalidDimension(name: string) {
    setSnapFilters((prev) => ({ ...prev, invalid_dimension: name, has_invalid: false }))
  }

  return (
    <section className="page" data-module="filterpreset">
      <header className="page-head">
        <div>
          <h2>筛选预设管理</h2>
          <p className="page-desc">管理分析查询的筛选条件预设，套用后生成筛选条件数据快照，失效维度可定位、可追溯。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('预设模板登记入口尚未接入审批流')}>
            登记预设模板
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出筛选预设清单
          </button>
        </div>
      </header>

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
                <button className="link" type="button" onClick={() => { setApplyTarget(row); setApplyMessage('') }}>
                  套用
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无筛选预设数据，可先登记预设模板</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条筛选预设记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {applyTarget && (
        <div className="panel">
          <h3>套用预设：{String(applyTarget['预设名称'])}（{String(applyTarget['预设编号'])}）</h3>
          <p className="panel-desc">
            套用会解析维度条件组、经维度映射改写旧字段名，与显式条件冲突时按「请求显式条件优先」裁决，全程分阶段生成快照。
          </p>
          <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void applyPreset() }}>
            <label className="filter-item">
              <span>显式条件（可选，如 省份=江苏;渠道=APP）</span>
              <input
                value={applyForm.显式条件}
                placeholder="与预设冲突时显式条件优先"
                onChange={(event) => setApplyForm((prev) => ({ ...prev, 显式条件: event.target.value }))}
              />
            </label>
            <label className="filter-item">
              <span>消费方</span>
              <input
                value={applyForm.消费方}
                onChange={(event) => setApplyForm((prev) => ({ ...prev, 消费方: event.target.value }))}
              />
            </label>
            <label className="filter-item">
              <span>演练中断阶段（可选）</span>
              <select
                value={applyForm.演练中断阶段}
                onChange={(event) => setApplyForm((prev) => ({ ...prev, 演练中断阶段: event.target.value }))}
              >
                <option value="">不中断</option>
                {STAGES.map((stage) => <option key={stage} value={stage}>{stage}</option>)}
              </select>
            </label>
            <button className="btn primary" type="submit">确认套用</button>
            <button className="btn ghost" type="button" onClick={() => setApplyTarget(null)}>取消</button>
          </form>
        </div>
      )}
      {applyMessage && <p className="panel-desc">{applyMessage}</p>}

      <div className="panel">
        <h3>筛选条件快照</h3>
        <p className="panel-desc">
          每次套用生成一份快照：生效条件、失效维度、失效显示列、冲突裁决与使用次数全部留痕；中断的快照只从最后一个完整阶段恢复。
        </p>
        <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reloadSnapshots() }}>
          <label className="filter-item">
            <span>关键字</span>
            <input
              value={snapFilters.keyword}
              placeholder="快照编号 / 预设编号 / 预设名称"
              onChange={(event) => setSnapFilters((prev) => ({ ...prev, keyword: event.target.value }))}
            />
          </label>
          <label className="filter-item">
            <span>状态</span>
            <select
              value={snapFilters.status}
              onChange={(event) => setSnapFilters((prev) => ({ ...prev, status: event.target.value }))}
            >
              <option value="">全部</option>
              {snapshotStatuses.map((status) => <option key={status} value={status}>{status}</option>)}
            </select>
          </label>
          <label className="filter-item">
            <span>失效维度名</span>
            <input
              value={snapFilters.invalid_dimension}
              placeholder="按失效维度定位快照"
              onChange={(event) => setSnapFilters((prev) => ({ ...prev, invalid_dimension: event.target.value }))}
            />
          </label>
          <label className="filter-item">
            <span>仅含失效</span>
            <input
              type="checkbox"
              checked={snapFilters.has_invalid}
              onChange={(event) => setSnapFilters((prev) => ({ ...prev, has_invalid: event.target.checked }))}
            />
          </label>
          <button className="btn" type="submit">查询</button>
          <button className="btn ghost" type="button" onClick={() => setSnapFilters({ keyword: '', status: '', invalid_dimension: '', has_invalid: false })}>重置</button>
        </form>

        <table className="data-table">
          <thead>
            <tr>
              <th>快照编号</th>
              <th>预设编号</th>
              <th>消费方</th>
              <th>当前阶段</th>
              <th>生效条件</th>
              <th>失效维度</th>
              <th>失效显示列</th>
              <th>使用次数</th>
              <th>状态</th>
              <th>创建时间</th>
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            {snapshots.map((snap) => (
              <Fragment key={snap.快照编号}>
                <tr>
                  <td>{snap.快照编号}</td>
                  <td>{snap.预设编号}</td>
                  <td>{snap.消费方}</td>
                  <td>{snap.当前阶段}</td>
                  <td>{snap.生效条件.length} 条</td>
                  <td>
                    {snap.失效维度.length === 0 && <span className="tag ok">无</span>}
                    {snap.失效维度.map((item) => (
                      <span key={`${snap.快照编号}-${item.维度名}`} className="tag danger">{item.维度名}</span>
                    ))}
                  </td>
                  <td>
                    {snap.失效显示列.length === 0 && <span className="tag ok">无</span>}
                    {snap.失效显示列.map((item) => (
                      <span key={`${snap.快照编号}-${item.列名}`} className="tag danger">{item.列名}</span>
                    ))}
                  </td>
                  <td>{snap.使用次数}</td>
                  <td>{snap.status}</td>
                  <td>{snap.创建时间}</td>
                  <td className="row-actions">
                    <button className="link" type="button" onClick={() => setExpandedId(expandedId === snap.id ? null : snap.id)}>
                      {expandedId === snap.id ? '收起' : '详情'}
                    </button>
                    {snap.status === '生成中' && (
                      <button className="link" type="button" onClick={() => void runSnapshotAction('恢复生成', snap)}>
                        恢复生成
                      </button>
                    )}
                    {snap.status !== '已归档' && (
                      <button className="link" type="button" onClick={() => void runSnapshotAction('归档快照', snap)}>
                        归档快照
                      </button>
                    )}
                  </td>
                </tr>
                {expandedId === snap.id && (
                  <tr key={`${snap.快照编号}-detail`}>
                    <td colSpan={11}>                      <div className="stage-flow">
                        {STAGES.map((stage) => {
                          const done = snap.已完成阶段.includes(stage)
                          const current = snap.当前阶段 === stage
                          return <span key={stage} className={`stage-node${done ? ' done' : ''}${current ? ' current' : ''}`}>{stage}{done ? ' ✓' : ''}</span>
                        })}
                      </div>
                      {snap.生效条件.length > 0 && (
                        <table className="sub-table">
                          <thead>
                            <tr><th>生效条件</th><th>来源</th><th>映射路径</th></tr>
                          </thead>
                          <tbody>
                            {snap.生效条件.map((cond, index) => (
                              <tr key={`${snap.快照编号}-cond-${index}`}>
                                <td>{cond.维度} {cond.操作符} {cond.值}</td>
                                <td>{cond.来源}</td>
                                <td>{cond.映射路径}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                      {snap.失效维度.length > 0 && (
                        <table className="sub-table">
                          <thead>
                            <tr><th>失效维度</th><th>失效原因</th><th>来源</th></tr>
                          </thead>
                          <tbody>
                            {snap.失效维度.map((item, index) => (
                              <tr key={`${snap.快照编号}-inv-${index}`}>
                                <td><span className="tag danger">{item.维度名}</span></td>
                                <td>{item.原因}</td>
                                <td>{item.来源 ?? '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                      {snap.失效显示列.length > 0 && (
                        <table className="sub-table">
                          <thead>
                            <tr><th>失效显示列</th><th>失效原因</th></tr>
                          </thead>
                          <tbody>
                            {snap.失效显示列.map((item) => (
                              <tr key={`${snap.快照编号}-col-${item.列名}`}>
                                <td><span className="tag danger">{item.列名}</span></td>
                                <td>{item.原因}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                      {snap.冲突裁决记录.length > 0 && (
                        <table className="sub-table">
                          <thead>
                            <tr><th>冲突维度</th><th>预设值</th><th>请求值</th><th>裁决</th><th>说明</th></tr>
                          </thead>
                          <tbody>
                            {snap.冲突裁决记录.map((item, index) => (
                              <tr key={`${snap.快照编号}-dec-${index}`}>
                                <td>{item.维度} {item.操作符}</td>
                                <td>{item.预设值 || '—'}</td>
                                <td>{item.请求值 || '—'}</td>
                                <td><span className="tag warn">{item.裁决}</span></td>
                                <td>{item.说明}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                      {snap.套用查询?.statement && <div className="mono">{snap.套用查询.statement}</div>}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {snapshots.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={11}>暂无筛选条件快照，点击预设行的「套用」生成</td>
              </tr>
            )}
          </tbody>
        </table>
        <footer className="page-foot">
          <span>共 {snapTotal} 条筛选条件快照</span>
        </footer>
      </div>

      <div className="panel">
        <h3>失效维度定位</h3>
        <p className="panel-desc">跨快照聚合失效维度，按出现次数排序；点击维度名可反查包含它的快照。</p>
        <table className="data-table">
          <thead>
            <tr>
              <th>维度名</th>
              <th>出现次数</th>
              <th>涉及预设</th>
              <th>涉及快照</th>
              <th>失效原因</th>
              <th>最近出现时间</th>
            </tr>
          </thead>
          <tbody>
            {invalidReport.map((item) => (
              <tr key={item.维度名}>
                <td>
                  <button className="link" type="button" onClick={() => locateInvalidDimension(item.维度名)}>
                    <span className="tag danger">{item.维度名}</span>
                  </button>
                </td>
                <td>{item.出现次数}</td>
                <td>{item.涉及预设.join('、')}</td>
                <td>{item.涉及快照.join('、')}</td>
                <td>{item.失效原因}</td>
                <td>{item.最近出现时间}</td>
              </tr>
            ))}
            {invalidReport.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={6}>暂无失效维度记录</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

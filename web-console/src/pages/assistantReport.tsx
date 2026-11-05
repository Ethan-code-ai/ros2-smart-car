import { useState } from "react";
import { useData } from "../hooks/useData";
import { businessApi } from "../services/businessApi";
import { EmptyState, Panel } from "../components/ui";
import { Link } from "../lib/router";

const coverageLabels: Record<string, string> = {
  observed: "观察到正常上报", interrupted: "上报中断或待补传", unknown: "覆盖未知",
};

export function AssistantReportPage({id}: {id: string}) {
  const report = useData(() => businessApi.assistantReport(id), [id]);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [showCoverage, setShowCoverage] = useState(false);
  async function download() {
    setDownloading(true);
    setError("");
    try {
      const blob = await businessApi.assistantReportMarkdown(id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `insightrover-${id}.md`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setError(error instanceof Error ? error.message : "无法导出报告");
    } finally {
      setDownloading(false);
    }
  }
  const data = report.data;
  if (!data) return <div className="page"><EmptyState title={report.loading ? "正在读取报告" : "报告不可用"} hint={report.error} /></div>;
  const focus = new Set(data.focus_ids ?? []);
  const evidence = [...data.details].sort((left, right) => Number(focus.has(right.id)) - Number(focus.has(left.id)));
  return <div className="page">
    <Link to="/assistant/history">返回助手历史</Link>
    <h1>异常事件报告</h1>
    <p>上海时间 {data.start} 至 {data.end}，共 {data.total} 条。</p>
    {data.missing_occurrence_time > 0 && <p>{data.missing_occurrence_time} 条记录缺少发生时间，未计入统计。</p>}
    <button type="button" className="btn btn-outline" disabled={downloading} onClick={() => void download()}>导出 Markdown</button>
    {error && <p role="alert">{error}</p>}
    <Panel title="分类与处理状态">
      <dl>{Object.entries(data.by_type).map(([key, value]) => <div key={key}><dt>{data.type_labels[key] ?? key}</dt><dd>{value}</dd></div>)}</dl>
      <dl>{Object.entries(data.by_status).map(([key, value]) => <div key={key}><dt>{data.status_labels[key] ?? key}</dt><dd>{value}</dd></div>)}</dl>
    </Panel>
    <Panel title="事件与证据">
      {data.truncated && <p>报告列出最近 {data.details.length} 条证据，统计包含全部记录。</p>}
      {evidence.length === 0 && <p>此时间范围内没有异常记录。</p>}
      {evidence.map(event => <div key={event.id} className="mini-row">
        <span><strong>{focus.has(event.id) ? "重点 · " : ""}{event.title}</strong>
          <em>{event.occurred_at} · {event.type_label} · {event.status_label} · {event.source}</em></span>
        <Link to={event.evidence_path}>查看事件 {event.id}</Link>
      </div>)}
    </Panel>
    <Panel title="采集覆盖">
      <p>{data.coverage_note}</p>
      {data.coverage.slice(0, showCoverage ? data.coverage.length : 12).map(item =>
        <p key={item.start}>{item.start} 至 {item.end} · {coverageLabels[item.state] ?? "覆盖未知"}</p>)}
      {!showCoverage && data.coverage.length > 12 &&
        <button type="button" className="btn btn-outline" onClick={() => setShowCoverage(true)}>查看全部时段</button>}
    </Panel>
  </div>;
}

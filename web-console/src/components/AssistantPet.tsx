import { useEffect, useRef, useState, type PointerEvent } from "react";
import { businessApi, readAssistantEvents, type AssistantExecution, type BusinessUser } from "../services/businessApi";
import { Link } from "../lib/router";
import { AssistantConversation } from "./AssistantConversation";
import "./assistantPet.css";

type Point = { x: number; y: number };
type Notice = { key: string; text: string; taskId?: string; found?: true };
type PendingPlan = { id: string; sessionId: string; expiresAt?: number };
const size = 64;

function restorePending(key: string): PendingPlan | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? "null");
    if (typeof value?.id !== "string" || typeof value?.sessionId !== "string" ||
        value.id.length > 128 || value.sessionId.length > 128) return null;
    if (value.expiresAt !== undefined && (!Number.isFinite(value.expiresAt) || value.expiresAt <= Date.now())) return null;
    return { id: value.id, sessionId: value.sessionId, ...(value.expiresAt ? { expiresAt: value.expiresAt } : {}) };
  } catch { return null; }
}

function restoreNotices(key: string) {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? "[]");
    if (Array.isArray(value)) return new Set<string>(value.filter(item => typeof item === "string").slice(-64));
  } catch { /* Storage is optional. */ }
  return new Set<string>();
}

function workspaceTop() {
  return (Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--header-h")) || 56) + 8;
}

function clamp({ x, y }: Point): Point {
  return {
    x: Math.max(8, Math.min(x, window.innerWidth - size - 8)),
    y: Math.max(workspaceTop(), Math.min(y, window.innerHeight - size - 8)),
  };
}

export function AssistantPet({ user }: { user: BusinessUser }) {
  const storageKey = `insightrover.pet-position.${user.id}`;
  const pendingKey = `insightrover.pet-pending.${user.id}`;
  const noticesKey = `insightrover.pet-notices.${user.id}`;
  const [savedPosition, setSavedPosition] = useState(() => {
    try {
      const point = JSON.parse(localStorage.getItem(storageKey) ?? "null");
      if (Number.isFinite(point?.x) && Number.isFinite(point?.y)) return point as Point;
    } catch { /* Storage is optional. */ }
    return clamp({ x: window.innerWidth - 88, y: window.innerHeight - 88 });
  });
  const [viewport, setViewport] = useState({ width: window.innerWidth, height: window.innerHeight });
  const position = clamp(savedPosition);
  const [panel, setPanel] = useState({ open: false, initialized: false });
  const { open, initialized } = panel;
  const [querying, setQuerying] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [status, setStatus] = useState("正在查询车端状态…");
  const [pendingPlan, setPendingPlan] = useState(() => restorePending(pendingKey));
  const restoredPending = useRef(pendingPlan);
  const [executionUpdate, setExecutionUpdate] = useState<AssistantExecution>();
  const drag = useRef<{ id: number; start: Point; origin: Point; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const seen = useRef(restoreNotices(noticesKey));
  const launcher = useRef<HTMLButtonElement>(null);
  const permissions = user.role?.permissions.map(p => p.name) ?? [];
  const canRead = permissions.includes("robots.read");
  const canReadMissions = permissions.includes("missions.read");
  const canReadAssistant = permissions.includes("assistant.read");
  const canUseAssistant = permissions.includes("assistant.use");

  useEffect(() => {
    try {
      if (pendingPlan) localStorage.setItem(pendingKey, JSON.stringify(pendingPlan));
      else localStorage.removeItem(pendingKey);
    } catch { /* Storage is optional. */ }
  }, [pendingKey, pendingPlan]);

  useEffect(() => {
    const saved = restoredPending.current;
    if (!saved) return;
    if (!canReadAssistant || !canUseAssistant) {
      setPendingPlan(null);
      return;
    }
    const controller = new AbortController();
    businessApi.assistantPlan(saved.id, controller.signal).then(plan => {
      const waiting = plan.execution?.state === "waiting_human" ||
        (plan.state === "draft" && plan.expires_at * 1000 > Date.now());
      if (!controller.signal.aborted && !waiting) setPendingPlan(current => current === saved ? null : current);
    }).catch(error => {
      if (!controller.signal.aborted && (error.status === 403 || error.status === 404)) {
        setPendingPlan(current => current === saved ? null : current);
      }
    });
    return () => controller.abort();
  }, [canReadAssistant, canUseAssistant]);

  useEffect(() => {
    if (!canReadAssistant) return;
    const controller = new AbortController();
    const cursorKey = `insightrover.assistant-cursor.${user.id}`;
    let cursor = 0;
    try {
      const value = Number(localStorage.getItem(cursorKey));
      if (Number.isSafeInteger(value) && value >= 0) cursor = value;
    } catch { /* Storage is optional. */ }
    let timer: ReturnType<typeof setTimeout>;
    async function checkPlan(planId: string) {
      try {
        return await businessApi.assistantPlan(planId, AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]));
      } catch (error) {
        if (error instanceof Error && "status" in error && (error.status === 403 || error.status === 404)) return;
        throw error;
      }
    }
    async function connect() {
      try {
        await readAssistantEvents(cursor, async (id, event) => {
          const payload = event.payload;
          if (event.kind.startsWith("execution.") && typeof payload.plan_id === "string" && typeof payload.version === "number") {
            setExecutionUpdate(payload as unknown as AssistantExecution);
            if (canUseAssistant && event.kind === "execution.waiting_human" && typeof payload.session_id === "string") {
              const planId = payload.plan_id, sessionId = payload.session_id;
              const plan = await checkPlan(planId);
              if (!controller.signal.aborted && plan?.execution?.state === "waiting_human" &&
                  plan.execution.version === payload.version) {
                setPendingPlan({id: planId, sessionId});
                setPanel({ open: true, initialized: true });
              }
            } else {
              setPendingPlan(current => current?.id === payload.plan_id ? null : current);
            }
          }
          if (canUseAssistant && event.kind === "plan.confirmation_required" &&
              typeof payload.id === "string" && typeof payload.session_id === "string" &&
              typeof payload.expires_at === "number" && payload.expires_at * 1000 > Date.now()) {
            const plan = await checkPlan(payload.id);
            if (!controller.signal.aborted && plan?.state === "draft" && plan.expires_at * 1000 > Date.now()) {
              setPendingPlan({id: payload.id, sessionId: payload.session_id, expiresAt: plan.expires_at * 1000});
              setPanel({ open: true, initialized: true });
            }
          }
          if (!controller.signal.aborted) {
            cursor = id;
            try { localStorage.setItem(cursorKey, String(cursor)); } catch { /* Storage is optional. */ }
          }
        }, controller.signal);
      } catch { /* Reconnect with the last received event ID. */ }
      if (!controller.signal.aborted) timer = setTimeout(connect, 1200);
    }
    void connect();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [canReadAssistant, canUseAssistant, user.id]);

  useEffect(() => {
    if (!pendingPlan?.expiresAt) return;
    const timer = setTimeout(() => setPendingPlan(current => current === pendingPlan ? null : current),
      Math.max(0, pendingPlan.expiresAt - Date.now()));
    return () => clearTimeout(timer);
  }, [pendingPlan]);

  useEffect(() => {
    function resize() {
      setViewport({ width: window.innerWidth, height: window.innerHeight });
    }
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);

  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(savedPosition)); } catch { /* Storage is optional. */ }
  }, [storageKey, savedPosition]);

  useEffect(() => {
    if (!canRead) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result = await businessApi.getRobotContext("r-001", controller.signal);
        if (controller.signal.aborted) return;
        const data = result.available ? result.telemetry : null;
        setStatus(data ? data.emergency_stop ? "车辆处于急停状态" :
          data.navigation?.message || `车辆模式：${data.mode ?? "未知"}` : "车端状态未确认");
        const task = data?.navigation_task;
        const search = data?.search;
        let next: Notice | null = null;
        if (canReadMissions && task?.kind === "delivery" && task.run_id && task.task_id &&
            (task.state === "waiting_load" || task.state === "waiting_handover")) {
          next = { key: `${task.run_id}:${task.state}`, taskId: task.task_id,
            text: task.state === "waiting_load" ? "运输任务等待确认装件" : "运输任务等待确认交接" };
        } else if (search?.state === "found" && search.run_id) {
          next = { key: `${search.run_id}:found`, found: true, text: "目标已找到，车端已确认停车，等待人工处理" };
        }
        setNotice(next);
        if (next && !seen.current.has(next.key)) {
          if (seen.current.size >= 64) seen.current.delete(seen.current.values().next().value!);
          seen.current.add(next.key);
          try { localStorage.setItem(noticesKey, JSON.stringify([...seen.current])); } catch { /* Storage is optional. */ }
          setPanel({ open: true, initialized: true });
        }
      } catch {
        if (!controller.signal.aborted) {
          setStatus("车端状态未确认");
          setNotice(null);
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 2500);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [canRead, canReadMissions, noticesKey]);

  function start(event: PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0 || !event.isPrimary) return;
    suppressClick.current = false;
    drag.current = { id: event.pointerId, start: { x: event.clientX, y: event.clientY }, origin: position, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function move(event: PointerEvent<HTMLButtonElement>) {
    const active = drag.current;
    if (!active || active.id !== event.pointerId) return;
    const dx = event.clientX - active.start.x, dy = event.clientY - active.start.y;
    if (!active.moved && Math.hypot(dx, dy) < 5) return;
    active.moved = true;
    suppressClick.current = true;
    setSavedPosition(clamp({ x: active.origin.x + dx, y: active.origin.y + dy }));
  }
  function end() { drag.current = null; }
  function close() { setPanel({ open: false, initialized: true }); launcher.current?.focus(); }

  if (!canRead) return null;
  const width = Math.min(370, viewport.width - 16);
  const left = Math.max(8, Math.min(position.x + size - width, viewport.width - width - 8));
  const aboveSpace = position.y - 12 - workspaceTop();
  const belowSpace = viewport.height - position.y - size - 20;
  const above = aboveSpace >= belowSpace;
  const top = above ? position.y - 12 : position.y + size + 12;
  const maxHeight = Math.max(0, Math.min(460, above ? aboveSpace : belowSpace));
  const found = notice?.found || (pendingPlan && executionUpdate?.plan_id === pendingPlan.id &&
    executionUpdate.state === "waiting_human" && executionUpdate.vehicle_state === "found");
  const mood = found ? "found" : notice || pendingPlan ? "waiting" : querying ? "querying" : "idle";
  return <div className="assistant-pet">
    {initialized && <section id="assistant-popover" className="assistant-popover" role="dialog" aria-label="智巡助手" hidden={!open}
      style={{ left, top, width, maxHeight, transform: above ? "translateY(-100%)" : undefined }}
      onKeyDown={event => { if (event.key === "Escape") close(); }}>
      <header><strong>智巡助手</strong><button type="button" aria-label="关闭对话窗" onClick={close}>×</button></header>
      {notice && <div className="assistant-notice" role="status">{notice.text}
        {notice.taskId && <Link to={`/tasks/${encodeURIComponent(notice.taskId)}`}>打开任务确认</Link>}
      </div>}
      <p>{status}</p>
      {canReadAssistant && <>
        <AssistantConversation canSend={canUseAssistant} preferredSession={pendingPlan?.sessionId} update={executionUpdate}
          onQueryingChange={setQuerying} />
        <Link to="/assistant/history" onClick={close}>查看会话历史</Link>
      </>}
    </section>}
    <button ref={launcher} type="button" className="assistant-launcher" data-mood={mood} aria-label="智巡桌宠，点击展开，按住拖动"
      aria-expanded={open} aria-controls={open ? "assistant-popover" : undefined}
      style={{ left: position.x, top: position.y }} onPointerDown={start} onPointerMove={move}
      onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end}
      onClick={event => {
        if (event.detail === 0 || !suppressClick.current) setPanel(value => ({ open: !value.open, initialized: true }));
      }}>
      <svg viewBox="0 0 64 64" aria-hidden="true">
        <g className="assistant-avatar-body">
        <rect x="8" y="42" width="13" height="14" rx="5" fill="#172b48"/>
        <rect x="43" y="42" width="13" height="14" rx="5" fill="#172b48"/>
        <rect x="9" y="20" width="46" height="31" rx="13" fill="#2870db"/>
        <rect x="14" y="24" width="36" height="20" rx="8" fill="#122443"/>
        <g className="assistant-avatar-eyes" fill="#baf3ff">
          <rect x="23" y="30" width="5" height="9" rx="2.5"/>
          <rect x="36" y="30" width="5" height="9" rx="2.5"/>
        </g>
        <rect x="23" y="12" width="18" height="9" rx="3" fill="#263957"/>
        <path className="assistant-avatar-radar" d="M24 9Q32 2 40 9M28 9Q32 6 36 9" fill="none" stroke="#2870db" strokeWidth="2" strokeLinecap="round"/>
        </g>
        <path className="assistant-avatar-star" d="M51 7L53 12 58 14 53 16 51 21 49 16 44 14 49 12Z" fill="#d68212"/>
      </svg>
      {(notice || pendingPlan) && <span className="assistant-pet-badge" aria-label="有待确认消息">!</span>}
    </button>
  </div>;
}

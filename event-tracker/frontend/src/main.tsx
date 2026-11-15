import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, json } from './api';
import type { Topic, Source, Dashboard, Notification, RunDetail, EventDetail } from './types';
import './style.css';

const active = (status?:string) => status === 'pending' || status === 'running';
const pretty = (value:unknown) => JSON.stringify(value, null, 2);
const date = (value:string) => new Date(value).toLocaleString();

function App() {
  const [health, setHealth] = useState('Connecting…');
  const [topics, setTopics] = useState<Topic[]>([]);
  const [selected, setSelected] = useState('');
  const [dashboard, setDashboard] = useState<Dashboard|null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [approved, setApproved] = useState<string[]>([]);
  const [notices, setNotices] = useState<Notification[]>([]);
  const [run, setRun] = useState<RunDetail|null>(null);
  const [event, setEvent] = useState<EventDetail|null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [title, setTitle] = useState('ExampleSoft security');
  const [description, setDescription] = useState('Track ExampleSoft vulnerabilities, affected versions and patches.');
  const [preference, setPreference] = useState('standard');
  const [batch, setBatch] = useState('1');
  const [provider, setProvider] = useState('mock');
  const running = active(dashboard?.recent_runs[0]?.status) || active(run?.run.status);

  useEffect(() => {
    api<{status:string}>('/health').then(h => setHealth(h.status)).catch(e => setHealth(String(e)));
    api<Topic[]>('/topics').then(items => {setTopics(items); setSelected(items[0]?.topic_id ?? '');}).catch(e => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    let disposed = false;
    let timer:ReturnType<typeof setTimeout>;
    setDashboard(null); setRun(null); setEvent(null); setError(''); setLoading(true);
    async function refresh() {
      try {
        const [d, recommendations, notifications] = await Promise.all([
          api<Dashboard>(`/topics/${selected}/dashboard`),
          api<{sources:Source[]}>(`/topics/${selected}/source-recommendations`, json('POST', {})),
          api<Notification[]>(`/topics/${selected}/notifications`),
        ]);
        if (disposed) return;
        setDashboard(d); setSources(recommendations.sources); setNotices(notifications);
        setApproved(d.approved_sources.map(s => s.source_id));
        const latest = d.recent_runs[0];
        if (latest) {
          const detail = await api<RunDetail>(`/runs/${latest.run_id}`);
          if (disposed) return;
          setRun(detail);
          if (active(detail.run.status)) timer = setTimeout(refresh, 1000);
        }
      } catch (e) { if (!disposed) setError(String(e)); }
      finally { if (!disposed) setLoading(false); }
    }
    void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, [selected]);

  async function reload() {
    const d = await api<Dashboard>(`/topics/${selected}/dashboard`);
    setDashboard(d);
    setNotices(await api<Notification[]>(`/topics/${selected}/notifications`));
  }
  async function action(task:()=>Promise<void>) {
    setBusy(true); setError('');
    try { await task(); } catch(e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function startRun() {
    const result = await api<{run_id:string}>(`/topics/${selected}/runs`, json('POST', {fixture_batch_id:batch,provider}));
    setEvent(null);
    // Changing the refresh token remounts the dashboard poller below.
    setPollId(result.run_id);
    await reload();
  }
  const [pollId, setPollId] = useState('');
  useEffect(() => {
    if (!pollId || !selected) return;
    let disposed = false;
    let timer:ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const detail = await api<RunDetail>(`/runs/${pollId}`);
        if (disposed) return;
        setRun(detail);
        const [d,n] = await Promise.all([api<Dashboard>(`/topics/${selected}/dashboard`),api<Notification[]>(`/topics/${selected}/notifications`)]);
        if (disposed) return;
        setDashboard(d); setNotices(n);
        if (active(detail.run.status)) timer = setTimeout(poll, 1000);
      } catch(e) { if (!disposed) setError(String(e)); }
    }
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [pollId, selected]);

  return <div className="app">
    <header><div className="brand">◎ TraceRadar</div><span className="badge">LOCAL WORKSPACE</span><span className="health">API · {health}</span></header>
    <div className="banner">Simulated data · 手动运行 · Mock is a rule-based demo, not a model evaluation.</div>
    <div className="layout"><aside>
      <h2>Your topics</h2>
      {topics.length === 0 && <p>No topics yet. Create one below.</p>}
      {topics.map(t => <button className={`topic ${t.topic_id === selected ? 'selected' : ''}`} key={t.topic_id} onClick={() => {setPollId('');setSelected(t.topic_id);}}>{t.title}<small>{t.status}</small></button>)}
      <form onSubmit={e => {e.preventDefault();void action(async () => {
        const t = await api<Topic>('/topics',json('POST',{title,description,notification_preferences:preference}));
        setTopics(await api<Topic[]>('/topics')); setPollId(''); setSelected(t.topic_id);
      });}}><h3>Create a topic</h3>
        <label>Title<input required value={title} onChange={e=>setTitle(e.target.value)}/></label>
        <label>Tracking goal<textarea required value={description} onChange={e=>setDescription(e.target.value)}/></label>
        <label>Notifications<select value={preference} onChange={e=>setPreference(e.target.value)}><option value="standard">Standard</option><option value="digest_only">Digest only</option></select></label>
        <button className="primary" disabled={busy}>Create topic</button>
      </form>
    </aside><main>
      {error && <div role="alert" className="error">{error}</div>}
      {loading && <p>Loading topic…</p>}
      {!selected && <section><h1>Follow a topic. See what changes.</h1><p>Create a topic, approve its sources, then run the three demo batches.</p></section>}
      {dashboard && <>
        <div className="heading"><div><span className="eyebrow">TRACKING TOPIC</span><h1>{dashboard.topic.title}</h1><p>{dashboard.topic.description}</p></div><span className="badge">{dashboard.topic.status}</span></div>
        <section><h2>1. Approve sources</h2><p>Static recommendations · Only approved sources enter the workflow.</p>
          {sources.map(s=><label className="source" key={s.source_id}><input type="checkbox" disabled={busy||running} checked={approved.includes(s.source_id)} onChange={e=>setApproved(e.target.checked ? [...approved,s.source_id] : approved.filter(id=>id!==s.source_id))}/><span>{s.name}<small>{s.endpoint}</small></span></label>)}
          <button disabled={busy||running} onClick={()=>void action(async()=>{await api(`/topics/${selected}/sources`,json('PUT',{approved_source_ids:approved}));await reload();setTopics(await api<Topic[]>('/topics'));})}>Save approved sources</button>
          <label className="preference">Notification preference<select value={dashboard.topic.notification_preferences} disabled={busy} onChange={e=>void action(async()=>{await api(`/topics/${selected}`,json('PATCH',{notification_preferences:e.target.value}));await reload();})}><option value="standard">Standard</option><option value="digest_only">Digest only</option></select></label>
        </section>
        <section><h2>2. Run tracking</h2><div className="controls"><label>Information batch<select value={batch} onChange={e=>setBatch(e.target.value)}><option value="1">第一批信息 · Disclosure</option><option value="2">后续进展 · Versions & event B</option><option value="3">补丁与冲突 · Patch & review</option><option value="empty">Empty batch</option></select></label><label>Assessment provider<select value={provider} onChange={e=>setProvider(e.target.value)}><option value="mock">Mock · Content rules</option><option value="qwen">Qwen · Requires backend config</option></select></label><button className="primary" disabled={busy||running||!dashboard.approved_sources.length} onClick={()=>void action(startRun)}>{running?'Running…':'Run tracking'}</button></div></section>
        <div className="stats"><section><b>{dashboard.events.length}</b>Events</section>{(['immediate','digest','record_only'] as const).map(a=><section key={a}><b>{dashboard.notification_counts[a]}</b>{a.replace('_',' ')}</section>)}</div>
        <section><h2>3. Event state</h2>{!dashboard.events.length && <p>No events yet. Run the first information batch.</p>}<div className="events">{dashboard.events.map(e=><button className="event" key={e.event_id} onClick={()=>void action(async()=>setEvent(await api<EventDetail>(`/events/${e.event_id}`)))}><span className="badge">{e.state.current_status}</span><h3>{e.title}</h3><p>{e.state.key_facts.length} facts · {e.state.sources.length} evidence · v{e.version}</p><span>Inspect evidence →</span></button>)}</div></section>
        {event && <section><div className="heading"><h2>{event.event.title}</h2><button onClick={()=>setEvent(null)}>Close</button></div><h3>Current status: {event.event.state.current_status}</h3><h3>Key facts</h3>{event.event.state.key_facts.map((f,i)=><p key={i}>{f.fact}<small>Evidence: {f.evidence_ids.join(', ')}</small></p>)}<h3>Timeline</h3>{event.event.state.timeline.map((t,i)=><p key={i}>{date(t.occurred_at)} · {t.description}<small>Evidence: {t.evidence_ids.join(', ')}</small></p>)}<h3>Sources (Evidence IDs)</h3><pre>{pretty(event.event.state.sources)}</pre><h3>Evidence</h3>{event.evidence.map(e=><details key={e.evidence_id}><summary>{e.title} · {e.relation}</summary><p>{e.content}</p><p>Simulated URL: {e.url}</p><small>{e.evidence_id} · published {date(e.published_at)} · fetched {date(e.fetched_at)}</small></details>)}<h3>State history</h3>{event.history.map(h=><details key={h.transition_id}><summary>{date(h.committed_at)} · {h.before_state?.current_status ?? 'new'} → {h.after_state.current_status}</summary><pre>{pretty(h)}</pre></details>)}</section>}
        <section><h2>Needs review · {dashboard.reviews.length}</h2>{!dashboard.reviews.length && <p>No conflicts or unresolved clues.</p>}{dashboard.reviews.map(r=><details key={r.evidence.evidence_id}><summary>{r.evidence.title} · {r.assessment.association.type}</summary><p>{r.evidence.content}</p><pre>{pretty(r.assessment)}</pre></details>)}</section>
        <section><h2>Notifications · In app only</h2>{!notices.length && <p>No state-change notifications yet.</p>}{(['immediate','digest','record_only'] as const).map(a=><div key={a}><h3>{a.replace('_',' ')}</h3>{notices.filter(n=>n.action===a).map(n=><div className="notice" key={n.notification_id}><span className="badge">{n.priority}</span> {dashboard.events.find(e=>e.event_id===n.event_id)?.title}<small>{n.reason_codes.join(', ')} · {date(n.created_at)} · Transition {n.transition_id}</small></div>)}</div>)}</section>
        <section><h2>Run trace</h2>{dashboard.recent_runs.length>0 && <label>Inspect a run<select value={run?.run.run_id ?? ''} disabled={running} onChange={e=>void action(async()=>setRun(await api<RunDetail>(`/runs/${e.target.value}`)))}>{dashboard.recent_runs.map(r=><option key={r.run_id} value={r.run_id}>{r.provider} · batch {r.fixture_batch_id} · {r.status} · {r.run_id.slice(0,8)}</option>)}</select></label>}{run ? <><p><span className="badge">{run.run.provider}</span> {run.run.status} · {run.run.run_id}</p><pre>{pretty(run.run.counts)}</pre>{run.run.error != null && <pre className="error">{pretty(run.run.error)}</pre>}{run.steps.map(s=><details key={s.step_id}><summary>{s.sequence}. {s.step_name} · {s.status} · attempt {s.attempt}</summary><small>{date(s.started_at)} → {s.completed_at ? date(s.completed_at) : 'in progress'}</small><pre>{pretty(s)}</pre></details>)}</>:<p>No runs yet.</p>}</section>
      </>}
    </main></div><footer>TraceRadar · Local single-user MVP · ExampleSoft is fictional</footer>
  </div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);

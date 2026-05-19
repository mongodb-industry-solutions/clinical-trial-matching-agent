"use client";

import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
} from "recharts";

/* ── Brand colours ────────────────────────────────────────── */
const FOREST  = "#00684A";
const SPRING  = "#00ED64";
const SLATE   = "#001E2B";
const SLATE60 = "#3D4F58";
const SLATE30 = "#889397";
const BORDER  = "#E8EDEB";

const PHASE_PALETTE  = ["#00684A","#00835B","#00A36C","#00C97F","#00ED64","#71F6BA","#C1C7C6"];
const STATUS_PALETTE = { RECRUITING:"#00684A", COMPLETED:"#889397", ACTIVE_NOT_RECRUITING:"#00835B",
  NOT_YET_RECRUITING:"#71F6BA", TERMINATED:"#C13128", SUSPENDED:"#C13128", WITHDRAWN:"#C1C7C6" };

/* ── Helpers ──────────────────────────────────────────────── */
function fmt(s) {
  return String(s||"").split("_").map(w=>w[0]?.toUpperCase()+w.slice(1).toLowerCase()).join(" ");
}

function buildData(results) {
  const phases={}, statuses={}, conditions={}, sponsors={};
  for (const t of results) {
    if (t.phase)   phases[t.phase]   = (phases[t.phase]   || 0) + 1;
    if (t.status)  statuses[t.status] = (statuses[t.status] || 0) + 1;
    if (t.sponsor) sponsors[t.sponsor]= (sponsors[t.sponsor]|| 0) + 1;
    for (const c of t.conditions||[]) conditions[c]=(conditions[c]||0)+1;
  }
  const toArr = (m) => Object.entries(m).map(([name,value])=>({name,value})).sort((a,b)=>b.value-a.value);
  return {
    phases:     toArr(phases),
    statuses:   toArr(statuses),
    conditions: toArr(conditions).slice(0,10),
    sponsors:   toArr(sponsors).slice(0,10),
  };
}

/* ── Custom tooltip ───────────────────────────────────────── */
function MdbTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const { name, value } = payload[0];
  return (
    <div style={{ background:"#fff", border:`1px solid ${BORDER}`, borderRadius:6,
      padding:"8px 12px", fontSize:12.5, boxShadow:"0 4px 12px rgba(0,0,0,0.10)" }}>
      <div style={{ fontWeight:700, color:SLATE, marginBottom:2 }}>{fmt(name)}</div>
      <div style={{ color:SLATE60 }}>{value.toLocaleString()} trial{value!==1?"s":""}</div>
    </div>
  );
}

/* ── Section wrapper ──────────────────────────────────────── */
function ChartCard({ title, subtitle, children }) {
  return (
    <div style={{ background:"#fff", border:`1px solid ${BORDER}`, borderRadius:8,
      padding:"16px 18px", boxShadow:"0 1px 3px rgba(0,0,0,0.08)" }}>
      <div style={{ fontSize:13, fontWeight:700, color:SLATE, marginBottom:2 }}>{title}</div>
      {subtitle && <div style={{ fontSize:11.5, color:SLATE30, marginBottom:12 }}>{subtitle}</div>}
      {children}
    </div>
  );
}

/* ── Phase donut ──────────────────────────────────────────── */
function PhaseDonut({ data }) {
  if (!data.length) return <Empty />;
  const total = data.reduce((s,d)=>s+d.value,0);
  return (
    <div style={{ display:"flex", alignItems:"center", gap:24 }}>
      <ResponsiveContainer width={180} height={180}>
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%"
            innerRadius={52} outerRadius={80} paddingAngle={2} startAngle={90} endAngle={-270}>
            {data.map((_,i)=><Cell key={i} fill={PHASE_PALETTE[i%PHASE_PALETTE.length]}/>)}
          </Pie>
          <Tooltip content={<MdbTooltip />}/>
        </PieChart>
      </ResponsiveContainer>
      <div style={{ flex:1 }}>
        <div style={{ fontSize:11, color:SLATE30, fontWeight:700, textTransform:"uppercase", letterSpacing:"0.05em", marginBottom:8 }}>
          {total.toLocaleString()} total
        </div>
        {data.map((d,i)=>(
          <div key={d.name} style={{ display:"flex", alignItems:"center", gap:8, marginBottom:5 }}>
            <span style={{ width:10, height:10, borderRadius:"50%", background:PHASE_PALETTE[i%PHASE_PALETTE.length], flexShrink:0 }}/>
            <span style={{ fontSize:12.5, color:SLATE60, flex:1 }}>{fmt(d.name)}</span>
            <span style={{ fontSize:12.5, fontWeight:700, color:SLATE, fontVariantNumeric:"tabular-nums" }}>{d.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Status bar ───────────────────────────────────────────── */
function StatusBar({ data }) {
  if (!data.length) return <Empty />;
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} layout="vertical" margin={{ left:0, right:16, top:4, bottom:4 }}>
        <CartesianGrid horizontal={false} stroke={BORDER}/>
        <XAxis type="number" tick={{ fontSize:11, fill:SLATE30 }} tickLine={false} axisLine={false}/>
        <YAxis type="category" dataKey="name" tick={{ fontSize:11, fill:SLATE60 }} tickLine={false}
          axisLine={false} width={130} tickFormatter={fmt}/>
        <Tooltip content={<MdbTooltip />}/>
        <Bar dataKey="value" radius={[0,4,4,0]} maxBarSize={20}>
          {data.map((d)=>(
            <Cell key={d.name} fill={STATUS_PALETTE[d.name] || SLATE30}/>
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ── Horizontal bar (conditions / sponsors) ───────────────── */
function HorizontalBar({ data, color = FOREST }) {
  if (!data.length) return <Empty />;
  const max = data[0]?.value || 1;
  return (
    <div>
      {data.map((d,i)=>(
        <div key={d.name} style={{ display:"flex", alignItems:"center", gap:10, marginBottom:7 }}>
          <span style={{ width:18, fontSize:11, color:SLATE30, textAlign:"right", fontVariantNumeric:"tabular-nums", flexShrink:0 }}>
            {i+1}
          </span>
          <div style={{ flex:1, minWidth:0 }}>
            <div style={{ display:"flex", justifyContent:"space-between", marginBottom:3 }}>
              <span style={{ fontSize:12.5, color:SLATE, fontWeight:500, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", maxWidth:"80%" }}>
                {d.name}
              </span>
              <span style={{ fontSize:12, fontWeight:700, color, flexShrink:0, marginLeft:8 }}>{d.value}</span>
            </div>
            <div style={{ height:5, borderRadius:4, background:BORDER, overflow:"hidden" }}>
              <div style={{ height:"100%", borderRadius:4, background:color, width:`${(d.value/max)*100}%`, transition:"width 0.4s ease" }}/>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Empty() {
  return <div style={{ textAlign:"center", padding:"24px 0", fontSize:12.5, color:SLATE30 }}>No data</div>;
}

/* ── Main export ──────────────────────────────────────────── */
export default function TrialAnalytics({ results = [] }) {
  if (!results.length) {
    return (
      <div style={{ textAlign:"center", padding:"60px 24px", color:SLATE30 }}>
        <div style={{ fontSize:32, marginBottom:12 }}>📊</div>
        <div style={{ fontSize:15, fontWeight:600, color:SLATE60, marginBottom:6 }}>Run a search first</div>
        <div style={{ fontSize:13 }}>Analytics are computed from the current search results.</div>
      </div>
    );
  }

  const { phases, statuses, conditions, sponsors } = buildData(results);

  return (
    <div>
      <div style={{ fontSize:13, color:SLATE60, marginBottom:16 }}>
        Computed from <strong style={{ color:SLATE }}>{results.length.toLocaleString()}</strong> trials in the current search.
        All charts are driven by MongoDB Atlas Search results — no separate query needed.
      </div>

      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:14, marginBottom:14 }}>
        <ChartCard title="Phase Distribution" subtitle="By trial phase">
          <PhaseDonut data={phases}/>
        </ChartCard>
        <ChartCard title="Recruitment Status" subtitle="Overall status breakdown">
          <StatusBar data={statuses}/>
        </ChartCard>
      </div>

      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:14 }}>
        <ChartCard title="Top Conditions" subtitle="Most frequent disease areas">
          <HorizontalBar data={conditions} color={FOREST}/>
        </ChartCard>
        <ChartCard title="Top Sponsors" subtitle="Most active trial sponsors">
          <HorizontalBar data={sponsors} color="#00835B"/>
        </ChartCard>
      </div>

      <div style={{ marginTop:14, padding:"12px 16px", background:"#F9FBFA",
        border:`1px solid ${BORDER}`, borderRadius:8, fontSize:12, color:SLATE30,
        display:"flex", alignItems:"center", gap:8 }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={FOREST} strokeWidth="2">
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
        </svg>
        In production, replace with embedded MongoDB Atlas Charts for real-time aggregation pipelines across the full corpus.
      </div>
    </div>
  );
}

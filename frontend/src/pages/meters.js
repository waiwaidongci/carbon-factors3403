import {heading} from '../components/common/header.js';
import {table} from '../components/common/table.js';
import {esc,fmt} from '../utils/format.js';
import {saveMeter,saveReading,deleteReading,saveFactor,closeMonth,reopenMonth,pumpJobs} from '../api/business.js';
import {store,selectMeter} from '../stores/business.js';
import {action} from '../hooks/action.js';
const KIND={regular:'常规',change_out:'换表出',change_in:'换表入'};
const tzOptions=['Asia/Shanghai','Asia/Tokyo','Europe/Berlin','America/New_York','America/Los_Angeles','UTC'];
function badge(text,cls){return `<span class="status ${cls||''}">${esc(text)}</span>`}
function wallLocal(iso,tz){
 const p=new Intl.DateTimeFormat('en-CA',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).formatToParts(new Date(iso));
 const g=n=>p.find(x=>x.type===n).value;
 return `${g('year')}-${g('month')}-${g('day')}T${g('hour')==='24'?'00':g('hour')}:${g('minute')}`;
}
export function meters(c,d){
 const canWrite=store.session?.role!=='viewer';
 const meter=d.data.meters.find(m=>m.id===d.meterId)||d.data.meters[0]||null;
 if(meter&&meter.id!==d.meterId)selectMeter(meter.id);
 return heading('METER LEDGER / 07','跨月抄表与月度分摊','带时区时刻的累计读数按表时区实际时长拆月；夏令时自动计入；封账月原值冻结，迟到/更正差额结转下一开放月。')+
 `<div class="meters-wrap">
 <aside class="meter-list">
  <details ${meter?'':'open'}><summary>登记表计 / 换表建档</summary>
  <form id="meter-form" class="stack-form">
   <label>表计编号<input name="code" required placeholder="M-EAST-01"></label>
   <label>名称<input name="name" placeholder="东区主表"></label>
   <label>表时区(IANA)<select name="tz_name">${tzOptions.map(t=>`<option ${t==='Asia/Shanghai'?'selected':''}>${t}</option>`).join('')}</select></label>
   <label>单位<select name="unit"><option>kWh</option><option>MWh</option><option>Wh</option></select></label>
   <label>被换表(可留空)<select name="replaces_id"><option value="">无（新表）</option>${d.data.meters.map(m=>`<option value="${m.id}">${esc(m.code)}（${esc(m.tz_name)}）</option>`).join('')}</select></label>
   <button ${canWrite?'':'disabled'}>保存表计</button>
   <p class="hint">换表要求旧表末条为“换表出”、新表首条为“换表入”，缺边界拒绝入账。</p>
  </form></details>
  <h3>表计清单</h3>
  ${d.data.meters.length?d.data.meters.map(m=>`<button class="meter-pick ${meter?.id===m.id?'active':''}" data-id="${m.id}">${esc(m.code)}<small>${esc(m.tz_name)} · gen ${m.rebuild_generation}${m.replaces_id?' · 替代表 '+m.replaces_id:''}</small></button>`).join(''):'<div class="empty">尚无表计，先登记一块表。</div>'}
 </aside>
 <main class="meter-main">${meter?meterDetail(meter,d,canWrite):'<div class="empty">请选择或登记表计。</div>'}</main>
 </div>`;
}
function meterDetail(meter,d,canWrite){
 const readings=d.data.readings.filter(r=>r.meter_id===meter.id);
 const allocations=d.data.allocations.filter(a=>a.meter_id===meter.id);
 const adjustments=d.data.adjustments.filter(a=>a.meter_id===meter.id);
 const closed=new Set(d.data.month_closes.filter(x=>x.closed).map(x=>x.month));
 return `
 <section><h2>① 原始累计读数（${esc(meter.code)} · ${esc(meter.tz_name)}）</h2>
 <details open><summary>录入 / 更正读数</summary>
 <form id="reading-form" class="stack-form">
  <input type="hidden" name="id">
  <input type="hidden" name="meter_id" value="${meter.id}">
  <div class="form-grid">
   <label>读数时刻（表时区墙钟）<input name="read_at" required placeholder="2026-03-08T12:00"></label>
   <label>累计读数<input name="cumulative" type="number" step="0.000001" min="0" required></label>
   <label>类型<select name="kind"><option value="regular">常规</option><option value="change_out">换表出</option><option value="change_in">换表入</option></select></label>
   <label>备注<input name="note" maxlength="250"></label>
  </div>
  <button ${canWrite?'':'disabled'}>提交读数（校验入链并排队重算）</button>
  <p class="hint">时刻按表时区解释并转UTC存储，展示本地偏移；累计量倒退或时刻非递增将被拒绝。</p>
 </form></details>
 ${table(readings.map(r=>({编号:r.id,'表时区时刻':wallLocal(r.read_at,meter.tz_name),'UTC时刻':r.read_at,'本地偏移(分)':r.utc_offset_min,类型:KIND[r.kind]||r.kind,累计读数:r.cumulative,备注:r.note,
   操作:canWrite?`<button class="toggle rd-edit" data-id="${r.id}">更正</button><button class="toggle rd-del" data-id="${r.id}">删除</button>`:''})))} </section>
 <section><h2>② 月度分摊（按表时区月界 × 实际时长）</h2>
 ${table(allocations.map(a=>({月份:a.month,封账:closed.has(a.month)?badge('已封账','failed'):badge('开放'),原量kWh:fmt(a.raw),
   冻结因子:a.factor??'—',排放kg:fmt(a.emissions),代次:a.generation,作业:a.job_id??'—'})))}
 <p class="hint">各月原量之和严格等于相邻累计读数差之和；夏令时切换日按23/25小时真实时长计权。</p>
 </section>
 <section><h2>③ 冲补关系（封账月差额 → 下一开放月）</h2>
 ${adjustments.length?table(adjustments.map(a=>({编号:a.id,来源月:a.source_month,承接月:a.target_month,差额原量:fmt(a.delta_raw),差额排放:fmt(a.delta_emissions),原因:a.reason,作业:a.job_id,代次:a.generation,幂等键:a.idem_key}))):'<div class="empty">尚无冲补。仅当迟到/更正读数影响已封账月时才生成，重试不会重复。</div>'}
 </section>
 <section><h2>④ 月度冻结因子与封账</h2>
 <details><summary>登记月度因子</summary>
 <form id="factor-form" class="stack-form"><div class="form-grid">
  <label>月份<input name="month" required placeholder="2026-02" maxlength="7"></label>
  <label>kg/单位<input name="kg_per_unit" type="number" step="0.000001" min="0" required></label>
  <label>单位<select name="unit"><option>kWh</option><option>MWh</option><option>Wh</option></select></label>
 </div><button ${canWrite?'':'disabled'}>保存因子</button><p class="hint">封账即冻结；已冻结因子拒绝覆盖，旧核算保持不变。</p></form></details>
 ${table(d.data.month_factors.map(f=>({月份:f.month,单位:f.unit,因子:f.kg_per_unit,状态:f.frozen?badge('已冻结','failed'):badge('可更新'),封账:closed.has(f.month)?
  (canWrite?`<button class="toggle month-reopen" data-month="${f.month}">解封</button>`:'已封账')
  :(canWrite?`<button class="toggle month-close" data-month="${f.month}">封账</button>`:'开放')})))}
 ${canWrite?`<p><button id="pump" class="toggle">手动驱动重算队列（演示/应急）</button> <span class="hint">正常由 worker 每秒轮询，崩溃作业超时自动回收。</span></p>`:''}
 </section>
 <section><h2>⑤ 重算作业（代次 / 认领 / 幂等）</h2>
 ${table(d.data.meter_jobs.filter(j=>j.meter_id===meter.id).map(j=>({编号:j.id,类型:j.kind,状态:j.status,代次:j.generation,尝试:j.attempts,认领者:j.claimed_by||'—',错误:j.error||'—'})))}
 </section>`;
}
export function bindMeters(){
 const mf=document.querySelector('#meter-form');
 if(mf)mf.onsubmit=e=>{e.preventDefault();action(async()=>{
  const b=Object.fromEntries(new FormData(mf));
  b.replaces_id=b.replaces_id?Number(b.replaces_id):null;
  const m=await saveMeter(b);selectMeter(m.id);await reloadAndRender();
 })};
 const rf=document.querySelector('#reading-form');
 if(rf)rf.onsubmit=e=>{e.preventDefault();action(async()=>{
  const b=Object.fromEntries(new FormData(rf));const id=b.id;delete b.id;
  b.meter_id=Number(b.meter_id);b.cumulative=Number(b.cumulative);
  await saveReading(b,id?Number(id):null);rf.reset();rf.querySelector('[name=meter_id]').value=b.meter_id;await reloadAndRender();
 })};
 document.querySelectorAll('.meter-pick').forEach(btn=>btn.onclick=()=>action(async()=>{selectMeter(Number(btn.dataset.id));await reloadAndRender()}));
 document.querySelectorAll('.rd-edit').forEach(btn=>btn.onclick=()=>{
  const id=Number(btn.dataset.id);const row=store.data.readings.find(x=>x.id===id);
  const m=store.data.meters.find(x=>x.id===row?.meter_id);
  const f=document.querySelector('#reading-form');if(!row||!f)return;
  f.id.value=row.id;f.read_at.value=wallLocal(row.read_at,m?m.tz_name:'UTC');f.cumulative.value=row.cumulative;f.kind.value=row.kind;f.note.value=row.note||'';
  f.scrollIntoView({behavior:'smooth'});
 });
 document.querySelectorAll('.rd-del').forEach(btn=>btn.onclick=()=>action(async()=>{
  if(confirm('确认删除该读数？将触发受影响区间重建。')){await deleteReading(Number(btn.dataset.id));await reloadAndRender()}
 }));
 const ff=document.querySelector('#factor-form');
 if(ff)ff.onsubmit=e=>{e.preventDefault();action(async()=>{
  const b=Object.fromEntries(new FormData(ff));b.kg_per_unit=Number(b.kg_per_unit);
  await saveFactor(b);ff.reset();await reloadAndRender();
 })};
 document.querySelectorAll('.month-close').forEach(btn=>btn.onclick=()=>action(async()=>{await closeMonth(btn.dataset.month);await reloadAndRender()}));
 document.querySelectorAll('.month-reopen').forEach(btn=>btn.onclick=()=>action(async()=>{await reopenMonth(btn.dataset.month);await reloadAndRender()}));
 const pump=document.querySelector('#pump');
 if(pump)pump.onclick=()=>action(async()=>{const r=await pumpJobs();alert('本轮处理作业 '+r.processed+' 份');await reloadAndRender()});
}
async function reloadAndRender(){const {reload}=await import('../stores/business.js');const {render}=await import('../main.js');await reload();render()}

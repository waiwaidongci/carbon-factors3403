"""跨月分摊重算引擎。

并发与一致性约定：
- 每块表有单调递增 rebuild_generation，每次入数/更正 +1；作业携带代次，
  旧执行者晚到的写入会被代次检查拒绝（旧执行者晚到不覆盖新值）。
- 重建在单事务内对表行加锁，PG 用 advisory_xact_lock 串行化同表作业；
  冲补行以 idem_key 唯一约束幂等，重试不重复冲补。
- 开放月：直接替换原核算；已封账月：保留原值，差额作为冲补记入下一开放月。
- 按月逐步提交检查点，重算中断后重跑只会补齐缺失，天然可恢复。
"""
import time
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import text
from src.models.metering import Meter,MonthFactor,MonthClose,MeterMonthAllocation,Adjustment
from src.services.timelib import split_allocation,add_months,now_month
from src.services.meter_validation import build_chains
from src.services.audit import log
Q=Decimal('0.000001')
def d(v):return Decimal(str(v)).quantize(Q)
def is_postgres(db):return db.bind.dialect.name=='postgresql'
def lock_meter(db,meter_id):
 if is_postgres(db):db.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':meter_id})
 return db.query(Meter).filter(Meter.id==meter_id).with_for_update().first()
def closed_months(db):
 return {x.month for x in db.query(MonthClose).filter(MonthClose.closed==True).all()}
def factor_for(db,month):
 f=db.query(MonthFactor).filter_by(month=month).first()
 if not f:return None
 return Decimal(str(f.kg_per_unit)),f.frozen
def next_open_month(db,frm):
 closed=closed_months(db);cur=frm;cap=add_months(now_month(),120)
 while cur in closed:
  cur=add_months(cur,1)
  if cur>cap:raise HTTPException(409,'不存在可承接差额的开放月，请先解封后续月份')
 return cur
def emit_adjustment(db,meter_id,source_month,target_month,delta_raw,delta_emis,reason,job_id,generation):
 idem='%s|%s|%s|%s'%(meter_id,source_month,job_id,generation)
 old=db.query(Adjustment).filter_by(idem_key=idem).first()
 if old:return old
 obj=Adjustment(idem_key=idem,meter_id=meter_id,source_month=source_month,target_month=target_month,
  delta_raw=d(delta_raw),delta_emissions=d(delta_emis),reason=reason,job_id=job_id,generation=generation,created_at=time.time())
 db.add(obj);return obj
def upsert_allocation(db,meter_id,month,raw,factor,emissions,generation,job_id):
 obj=db.query(MeterMonthAllocation).filter_by(meter_id=meter_id,month=month).with_for_update().first()
 if obj and obj.generation>generation:return obj
 if not obj:
  obj=MeterMonthAllocation(meter_id=meter_id,month=month,raw=Decimal(0),generation=generation)
  db.add(obj);db.flush()
 obj.raw=d(raw);obj.factor=d(factor) if factor is not None else None
 obj.emissions=d(emissions) if emissions is not None else None
 obj.generation=generation;obj.job_id=job_id;obj.updated_at=time.time()
 return obj
def compute_targets(db,meter):
 """从当前读数链计算每月原量（未考虑封账），返回 {month: raw_decimal}。"""
 _,pairs=build_chains(db,meter)
 targets={}
 for a,b in pairs:
  delta=Decimal(str(b.cumulative))-Decimal(str(a.cumulative))
  if delta<0:raise HTTPException(422,'累计读数倒退，拒绝入账')
  for month,part in split_allocation(a.read_at,b.read_at,delta,meter.tz_name):
  # 注意 split_allocation 已保证分量之和等于段差
   targets[month]=targets.get(month,Decimal(0))+part
 return {m:d(v) for m,v in sorted(targets.items())}
def reconcile_adjustment(db,meter_id,source_month,delta,factor,job_id,generation):
 """把封账月差额收敛为单一有效冲补。

 已落到（后来也被）封账月份的历史冲补视为锁定，只补剩余差额；
 目标仍开放的旧冲补先冲销，再按当前差额重发，保证重复重算不累计、可收敛。
 """
 existing=db.query(Adjustment).filter_by(meter_id=meter_id,source_month=source_month).order_by(Adjustment.id).all()
 locked=Decimal(0)
 for a in existing:
  if a.target_month in closed_months(db):locked+=Decimal(str(a.delta_raw))
  else:db.delete(a)
 db.flush()
 need=(Decimal(str(delta))-locked).quantize(Q)
 if need==0:return None
 if factor is None:raise HTTPException(422,'封账月 %s 缺少当月冻结因子，无法核算差额'%source_month)
 target=next_open_month(db,source_month)
 return emit_adjustment(db,meter_id,source_month,target,need,(need*factor).quantize(Q),
  '迟到/更正读数；封账月原值保留，差额结转（含历史锁定冲补）',job_id,generation)
def rebuild_meter(db,meter_id,job_id,generation,actor='worker'):
 meter=lock_meter(db,meter_id)
 if not meter:raise HTTPException(404,'表计不存在')
 if meter.rebuild_generation>generation:
  return {'skipped':True,'reason':'stale_generation','meter_generation':meter.rebuild_generation,'job_generation':generation}
 closed=closed_months(db)
 targets=compute_targets(db,meter)
 months=set(targets)|{x.month for x in db.query(MeterMonthAllocation).filter_by(meter_id=meter_id).all()}
 changed=[]
 for month in sorted(months):
  new_raw=targets.get(month,Decimal(0))
  fac=factor_for(db,month)
  factor=fac[0] if fac else None
  new_emis=(new_raw*factor).quantize(Q) if factor is not None else None
  existing=db.query(MeterMonthAllocation).filter_by(meter_id=meter_id,month=month).with_for_update().first()
  old_raw=Decimal(str(existing.raw)) if existing else Decimal(0)
  if month in closed:
   # 已封账：原值冻结，差额收敛为单一有效冲补转下一开放月
   if not existing:raise HTTPException(409,'封账月 %s 缺少既有核算，无法承接重建'%month)
   delta=(new_raw-old_raw).quantize(Q)
   had_adj=db.query(Adjustment).filter_by(meter_id=meter_id,source_month=month).first()
   if delta!=0 or had_adj:
    adj=reconcile_adjustment(db,meter_id,month,delta,factor,job_id,generation)
    changed.append({'month':month,'closed':True,'delta_raw':str(delta),'adjustment_id':adj.id if adj else None})
   continue
  upsert_allocation(db,meter_id,month,new_raw,factor,new_emis,generation,job_id)
  changed.append({'month':month,'closed':False,'raw':str(new_raw),'emissions':str(new_emis) if new_emis is not None else None})
 db.commit()
 log(db,actor,'meter.rebuild','%s gen=%s months=%s'%(meter.code,generation,len(changed)))
 return {'skipped':False,'meter':meter.code,'generation':generation,'changed':changed}
def refresh_factor(db,month,actor='admin'):
 """因子更新：开放月重算排放；封账月只对未冻结因子留痕，不回改已核算。"""
 fac=db.query(MonthFactor).filter_by(month=month).with_for_update().first()
 if not fac:raise HTTPException(404,'该月因子不存在')
 factor=Decimal(str(fac.kg_per_unit))
 closed=db.query(MonthClose).filter_by(month=month,closed=True).first()
 touched=[]
 if closed:
  fac.frozen=True;fac.frozen_by=actor;fac.frozen_at=time.time()
 else:
  for al in db.query(MeterMonthAllocation).filter_by(month=month).all():
   al.factor=d(factor);al.emissions=(Decimal(str(al.raw))*factor).quantize(Q);al.updated_at=time.time()
   touched.append(al.id)
 db.commit();return touched

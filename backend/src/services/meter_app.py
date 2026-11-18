import time
from fastapi import HTTPException
from zoneinfo import ZoneInfo
from src.models.metering import Meter,MeterReading,MonthFactor,MonthClose,MeterMonthAllocation,Adjustment,MeterJob
from src.services.meter_validation import validate_meter_input,validate_reading_input,ordered_readings,check_reading_chain,validate_replacement
from src.services.meter_jobs import enqueue
from src.services.metering import refresh_factor,closed_months
from src.services.audit import log
def create_meter(db,user,data,id=None):
 code=validate_meter_input(data)
 replaces_id=data.get('replaces_id')
 if replaces_id:
  old=db.get(Meter,int(replaces_id))
  if not old:raise HTTPException(422,'被换表不存在')
  if old.tz_name!=data['tz_name']:raise HTTPException(422,'换表前后须保持同一表时区')
 obj=db.get(Meter,id) if id else None
 if id and not obj:raise HTTPException(404,'表计不存在')
 dup=db.query(Meter).filter(Meter.code==code)
 if id:dup=dup.filter(Meter.id!=id)
 if dup.first():raise HTTPException(409,'表计编号已存在')
 if not obj:obj=Meter(code=code,created=time.time())
 obj.code=code;obj.name=str(data.get('name') or code)[:160];obj.tz_name=data['tz_name']
 obj.unit=data.get('unit','kWh');obj.replaces_id=int(replaces_id) if replaces_id else None
 db.add(obj);db.flush()
 # 换表链边界立即校验（新表尚无读数时只检查旧表 change_out）
 if replaces_id:
  olds=ordered_readings(db,obj.replaces_id)
  if not olds or olds[-1].kind!='change_out':
   db.rollback();raise HTTPException(422,'旧表缺少换表出(change_out)边界读数，拒绝建档')
 log(db,user.email,'meter.upsert',obj.code);db.commit();return obj
def save_reading(db,user,data,id=None):
 meter=db.get(Meter,int(data['meter_id'])) if isinstance(data,dict) and data.get('meter_id') else None
 if not meter:raise HTTPException(404,'表计不存在')
 instant,cum,kind,note=validate_reading_input(data,meter.tz_name)
 obj=db.get(MeterReading,id) if id else None
 if id and not obj:raise HTTPException(404,'读数不存在')
 conflict=db.query(MeterReading).filter(MeterReading.meter_id==meter.id,MeterReading.read_at==instant)
 if obj:conflict=conflict.filter(MeterReading.id!=obj.id)
 if conflict.first():raise HTTPException(409,'同一表该时刻已有读数')
 if not obj:obj=MeterReading(meter_id=meter.id,recorded_at=time.time())
 obj.read_at=instant;obj.tz_name=meter.tz_name
 loc=instant.astimezone(ZoneInfo(meter.tz_name))
 obj.utc_offset_min=int(loc.utcoffset().total_seconds()//60)
 obj.cumulative=cum;obj.kind=kind;obj.note=note;obj.updated_at=time.time()
 db.add(obj);db.flush()
 # 全链校验：倒退/时刻/换表边界，任何不合法整体回滚
 try:
  check_reading_chain(meter,ordered_readings(db,meter));validate_replacement(db,meter)
 except ValueError as exc:
  db.rollback();raise HTTPException(422,str(exc))
 job=enqueue(db,meter,payload={'reading_id':obj.id,'actor':user.email},actor=user.email)
 log(db,user.email,'meter.reading.%s'%('update' if id else 'create'),'%s@%s'%(meter.code,instant.isoformat()))
 db.commit();return obj,job
def delete_reading(db,user,id):
 obj=db.get(MeterReading,id)
 if not obj:raise HTTPException(404,'读数不存在')
 meter=db.get(Meter,obj.meter_id)
 db.delete(obj);db.flush()
 try:
  check_reading_chain(meter,ordered_readings(db,meter));validate_replacement(db,meter)
 except ValueError as exc:
  db.rollback();raise HTTPException(422,str(exc))
 job=enqueue(db,meter,payload={'reading_deleted':id,'actor':user.email},actor=user.email)
 log(db,user.email,'meter.reading.delete',id);db.commit();return job
def save_factor(db,user,data):
 month=str(data.get('month') or '').strip()
 if len(month)!=7 or month[4]!='-':raise HTTPException(422,'月份须为YYYY-MM')
 fac=db.query(MonthFactor).filter_by(month=month).first()
 if fac and fac.frozen:raise HTTPException(409,'%s 因子已随封账冻结，不能覆盖'%month)
 try:kg=__import__('decimal').Decimal(str(data['kg_per_unit']))
 except Exception:raise HTTPException(422,'因子数值不合法')
 if kg<0:raise HTTPException(422,'因子不能为负')
 unit=data.get('unit','kWh')
 if not fac:fac=MonthFactor(month=month);db.add(fac)
 fac.unit=unit;fac.kg_per_unit=kg;fac.updated_at=time.time()
 db.flush();refresh_factor(db,month,actor=user.email)
 log(db,user.email,'meter.factor.upsert',month);db.commit();return fac
def close_month(db,user,month):
 if len(month)!=7:raise HTTPException(422,'月份须为YYYY-MM')
 fac=db.query(MonthFactor).filter_by(month=month).with_for_update().first()
 if not fac:raise HTTPException(422,'%s 缺少当月因子，不能封账'%month)
 allocations=db.query(MeterMonthAllocation).filter_by(month=month).all()
 if not allocations:raise HTTPException(422,'%s 尚无任何分摊核算，不能空封账'%month)
 fac.frozen=True;fac.frozen_by=user.email;fac.frozen_at=time.time()
 close=db.query(MonthClose).filter_by(month=month).with_for_update().first()
 if not close:close=MonthClose(month=month);db.add(close)
 close.closed=True;close.closed_by=user.email;close.closed_at=time.time()
 log(db,user.email,'meter.month.close',month);db.commit();return close
def reopen_month(db,user,month):
 close=db.query(MonthClose).filter_by(month=month).with_for_update().first()
 if not close:raise HTTPException(404,'该月未封账')
 close.closed=False;log(db,user.email,'meter.month.reopen',month);db.commit();return close
def trace(db,meter_id):
 meter=db.get(Meter,meter_id)
 if not meter:raise HTTPException(404,'表计不存在')
 readings=ordered_readings(db,meter)
 allocations=db.query(MeterMonthAllocation).filter_by(meter_id=meter_id).order_by(MeterMonthAllocation.month).all()
 adjustments=db.query(Adjustment).filter_by(meter_id=meter_id).order_by(Adjustment.id).all()
 jobs=db.query(MeterJob).filter_by(meter_id=meter_id).order_by(MeterJob.id.desc()).limit(20).all()
 def al(a):return {'id':a.id,'month':a.month,'raw':str(a.raw),'factor':str(a.factor) if a.factor is not None else None,
  'emissions':str(a.emissions) if a.emissions is not None else None,'generation':a.generation,'job_id':a.job_id}
 def ad(a):return {'id':a.id,'source_month':a.source_month,'target_month':a.target_month,'delta_raw':str(a.delta_raw),
  'delta_emissions':str(a.delta_emissions),'reason':a.reason,'job_id':a.job_id,'generation':a.generation,'idem_key':a.idem_key}
 def rd(r):return {'id':r.id,'read_at_utc':r.read_at.isoformat(),'local_offset_min':r.utc_offset_min,
  'cumulative':str(r.cumulative),'kind':r.kind,'note':r.note}
 return {'meter':{'id':meter.id,'code':meter.code,'name':meter.name,'tz':meter.tz_name,'unit':meter.unit,
   'replaces_id':meter.replaces_id,'generation':meter.rebuild_generation},
  'readings':[rd(r) for r in readings],
  'allocations':[al(a) for a in allocations],
  'adjustments':[ad(a) for a in adjustments],
  'closed_months':sorted(closed_months(db)),
  'jobs':[{'id':j.id,'kind':j.kind,'status':j.status,'generation':j.generation,'error':j.error,'result':j.result} for j in jobs]}

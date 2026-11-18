"""抄表链构建与入账前校验。

规则：
- 每块物理表的读数按时刻严格递增；累计量倒退拒绝入账。
- 换表：旧表最后一条须为 change_out，新表第一条须为 change_in；
  缺换表边界拒绝入账，换表两侧各自独立成段，不跨表做差。
- 同一表同一时刻唯一。
"""
from decimal import Decimal
from fastapi import HTTPException
from src.constants.enums import READING_KINDS
from src.services.timelib import parse_instant,zone
def validate_meter_input(data):
 code=(data.get('code') or '').strip() if isinstance(data,dict) else ''
 if not code or len(code)>60:raise ValueError('表计编号不能为空或过长')
 if not isinstance(data.get('tz_name'),str) or not data['tz_name']:raise ValueError('必须指定IANA时区，如Asia/Shanghai')
 zone(data['tz_name'])
 unit=data.get('unit','kWh')
 if not isinstance(unit,str) or unit not in ('kWh','Wh','MWh'):raise ValueError('单位只接受kWh/Wh/MWh')
 return code
def validate_reading_input(data,tz_name):
 if not isinstance(data,dict):raise ValueError('读数必须为对象')
 kind=data.get('kind','regular')
 if kind not in READING_KINDS:raise ValueError('读数类型只能是regular/change_out/change_in')
 instant=parse_instant(data.get('read_at'),tz_name)
 cum=data.get('cumulative')
 if isinstance(cum,bool) or not isinstance(cum,(int,float,str)):raise ValueError('累计读数必须为数字')
 try:cum=Decimal(str(cum))
 except Exception as exc:raise ValueError('累计读数不合法') from exc
 if cum<0:raise ValueError('累计读数不能为负')
 note=str(data.get('note',''))[:250]
 return instant,cum,kind,note
def ordered_readings(db,meter):
 from src.models.metering import MeterReading,Meter
 mid=meter.id if hasattr(meter,'id') else int(meter)
 return db.query(MeterReading).filter_by(meter_id=mid).order_by(MeterReading.read_at,MeterReading.id).all()
def check_reading_chain(meter,readings):
 """对单表读数做完整规则校验，返回[(reading, 本地偏移分钟)]或抛ValueError。"""
 if not readings:return []
 prev=None
 for rd in readings:
  if prev is not None:
   if rd.read_at<=prev.read_at:raise ValueError('表 %s 存在非严格递增时刻：%s'%(meter.code,rd.read_at.isoformat()))
   if rd.kind=='change_in':raise ValueError('换表入(change_in)只能是新表首条读数')
   if Decimal(str(rd.cumulative))<Decimal(str(prev.cumulative)):
    raise ValueError('表 %s 在 %s 累计读数倒退：%s → %s'%(meter.code,rd.read_at.isoformat(),prev.cumulative,rd.cumulative))
  prev=rd
 first=readings[0]
 if first.kind=='change_out':raise ValueError('换表出(change_out)不能是首条读数')
 for rd in readings[1:]:
  if rd.kind=='change_in':raise ValueError('换表入(change_in)只能是新表首条读数')
 for rd in readings[:-1]:
  if rd.kind=='change_out':raise ValueError('换表出(change_out)只能是旧表末条读数')
 return readings
def validate_replacement(db,meter):
 from src.models.metering import Meter
 if meter.replaces_id:
  old=db.get(Meter,meter.replaces_id)
  if not old:raise ValueError('被换表不存在')
  olds=ordered_readings(db,old)
  news=ordered_readings(db,meter)
  if not olds or olds[-1].kind!='change_out':raise ValueError('旧表 %s 缺少换表出(change_out)边界读数，拒绝入账'%old.code)
  if not news or news[0].kind!='change_in':raise ValueError('新表 %s 缺少换表入(change_in)边界读数，拒绝入账'%meter.code)
def build_chains(db,meter):
 """返回该表可参与做差的相邻读数对，change_in 作为新表起点不产生负向段。"""
 readings=check_reading_chain(meter,ordered_readings(db,meter))
 validate_replacement(db,meter)
 pairs=[]
 for a,b in zip(readings,readings[1:]):
  if b.kind=='change_in' or a.kind=='change_out':continue
  pairs.append((a,b))
 return readings,pairs
def reject_http(exc):
 return HTTPException(422,str(exc))

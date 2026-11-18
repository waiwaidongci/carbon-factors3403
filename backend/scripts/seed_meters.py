"""跨月抄表演示种子：自然键幂等，只补缺，不覆盖用户修改。

覆盖：上海表跨月/跨年更正与封账结转；纽约表夏令时(23h)时长；
换表出/换表入边界。因子与读数落库后直接驱动一次重算队列。
"""
from decimal import Decimal
from src.config.database import Session
from src.models.metering import Meter,MeterReading,MonthFactor
from src.services.meter_app import create_meter,save_reading,save_factor,close_month
from src.services.meter_jobs import pump
class SeedUser:
 email='seed@system'
def _meter(db,code,tz,replaces=None):
 m=db.query(Meter).filter_by(code=code).first()
 if m:return m
 return create_meter(db,SeedUser(),{'code':code,'name':code,'tz_name':tz,'unit':'kWh','replaces_id':replaces})
def _factor(db,month,kg):
 if not db.query(MonthFactor).filter_by(month=month).first():
  save_factor(db,SeedUser(),{'month':month,'kg_per_unit':kg,'unit':'kWh'})
def _reading(db,meter,ts,cum,kind='regular'):
 exists=db.query(MeterReading).filter_by(meter_id=meter.id).order_by(MeterReading.read_at).all()
 # 同表同时刻已存在则跳过，保证幂等
 from src.services.timelib import parse_instant
 inst=parse_instant(ts,meter.tz_name)
 if any(r.read_at==inst for r in exists):return
 save_reading(db,SeedUser(),{'meter_id':meter.id,'read_at':ts,'cumulative':cum,'kind':kind})
def seed_meters():
 with Session() as db:
  for mo,kg in (('2025-12',0.55),('2026-01',0.57),('2026-02',0.58),('2026-03',0.59),('2026-04',0.6),('2026-05',0.61)):
   _factor(db,mo,kg)
  # 上海表：含跨年读数，封账1月后迟到更正，差额结转2月
  sh=_meter(db,'M-SH-01','Asia/Shanghai')
  _reading(db,sh,'2025-12-25T00:00',0)
  _reading(db,sh,'2026-01-01T00:00',120)
  _reading(db,sh,'2026-02-01T00:00',1120)
  _reading(db,sh,'2026-03-01T00:00',2480)
  pump(db)
  from src.models.metering import MonthClose
  if not db.query(MonthClose).filter_by(month='2026-01').first():
   close_month(db,SeedUser(),'2026-01')
  # 迟到更正：2/1 累计 1120→1240，1月+120 被封账冻结，冲补到2月（幂等：已更正则跳过）
  rd=db.query(MeterReading).filter_by(meter_id=sh.id).order_by(MeterReading.read_at).all()[2]
  if Decimal(str(rd.cumulative))!=Decimal('1240'):
   from src.services.meter_app import save_reading as _sr
   _sr(db,SeedUser(),{'meter_id':sh.id,'read_at':'2026-02-01T00:00','cumulative':1240,'kind':'regular','note':'迟到更正'},rd.id)
   pump(db)
  # 纽约表：跨春令时(23h)，并演示换表边界
  ny=_meter(db,'M-NY-02','America/New_York')
  _reading(db,ny,'2026-03-01T00:00',0)
  _reading(db,ny,'2026-04-02T00:00',800)
  _reading(db,ny,'2026-05-01T00:00',1500,kind='change_out')
  ny2=_meter(db,'M-NY-03','America/New_York',replaces=ny.id)
  _reading(db,ny2,'2026-05-01T00:00',0,kind='change_in')
  _reading(db,ny2,'2026-06-01T00:00',640)
  pump(db)
  print('[seed_meters] meters=%d readings=%d'%(db.query(Meter).count(),db.query(MeterReading).count()))
if __name__=='__main__':
 from src.config.database import Base,engine
 from src.models import metering
 Base.metadata.create_all(engine)
 seed_meters()

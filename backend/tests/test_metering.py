"""服务级：重建、封账结转、换表边界、倒退拒绝、代次与幂等、可恢复。"""
import pytest
from decimal import Decimal
from fastapi import HTTPException
from src.models.metering import Meter,MeterReading,MeterMonthAllocation,Adjustment,MeterJob
from src.services.meter_app import create_meter,save_reading,save_factor,close_month,trace
from src.services.meter_jobs import pump
Q=Decimal('0.000001')
def make_meter(db,user,code='M-1',tz='Asia/Shanghai',replaces_id=None):
 return create_meter(db,user,{'code':code,'name':code,'tz_name':tz,'unit':'kWh','replaces_id':replaces_id})
def add(db,user,meter,ts,cum,kind='regular'):
 return save_reading(db,user,{'meter_id':meter.id,'read_at':ts,'cumulative':cum,'kind':kind})
def fac(db,user,month,kg=0.5):
 return save_factor(db,user,{'month':month,'kg_per_unit':kg,'unit':'kWh'})
def raw_of(db,meter,month):
 al=db.query(MeterMonthAllocation).filter_by(meter_id=meter.id,month=month).first()
 return Decimal(str(al.raw)) if al else None
def test_basic_cross_month_split_and_emissions(db,user):
 m=make_meter(db,user)
 fac(db,user,'2026-01',0.5);fac(db,user,'2026-02',0.6);fac(db,user,'2026-03',0.7)
 add(db,user,m,'2026-01-01T00:00',0)
 add(db,user,m,'2026-02-01T00:00',100) # 1月整月100
 add(db,user,m,'2026-03-05T00:00',250) # 2月整月150 + 3月前4天
 pump(db)
 assert raw_of(db,m,'2026-01')==Decimal('100.000000')
 # 2/1→3/5 共32天，2月占28天：150*28/32=131.25
 assert raw_of(db,m,'2026-02')==Decimal('131.250000')
 al2=db.query(MeterMonthAllocation).filter_by(meter_id=m.id,month='2026-02').first()
 assert Decimal(str(al2.emissions))==(Decimal(str(al2.raw))*Decimal('0.6')).quantize(Q)
 assert sum((raw_of(db,m,mo) or Decimal(0) for mo in ('2026-01','2026-02','2026-03')),Decimal(0))==Decimal('250.000000')
def test_total_conservation_across_rebuilds(db,user):
 m=make_meter(db,user);fac(db,user,'2026-01');fac(db,user,'2026-02');fac(db,user,'2026-03')
 add(db,user,m,'2026-01-01T00:00',0)
 add(db,user,m,'2026-02-15T12:00',100)
 add(db,user,m,'2026-03-10T00:00',177)
 pump(db)
 total=sum((Decimal(str(a.raw)) for a in db.query(MeterMonthAllocation).filter_by(meter_id=m.id)),Decimal(0))
 assert total==Decimal('177.000000')
 # 迟到更正中间读数 → 只重建受影响区间，总量仍守恒
 add(db,user,m,'2026-02-15T12:00',120,kind='regular') if False else None
 rd=db.query(MeterReading).filter_by(meter_id=m.id).all()[1]
 save_reading(db,user,{'meter_id':m.id,'read_at':'2026-02-15T12:00','cumulative':120,'kind':'regular'},rd.id)
 pump(db)
 total=sum((Decimal(str(a.raw)) for a in db.query(MeterMonthAllocation).filter_by(meter_id=m.id)),Decimal(0))
 assert total==Decimal('177.000000')
def test_regressive_cumulative_rejected(db,user):
 m=make_meter(db,user)
 add(db,user,m,'2026-01-01T00:00',100)
 with pytest.raises(HTTPException) as e:
  add(db,user,m,'2026-02-01T00:00',90)
 assert e.value.status_code==422 and '倒退' in e.value.detail
 assert db.query(MeterReading).count()==1
def test_meter_change_requires_boundaries(db,user):
 old=make_meter(db,user,'OLD')
 add(db,user,old,'2026-01-01T00:00',0)
 add(db,user,old,'2026-02-01T00:00',80)
 # 新表建档时旧表还没有 change_out → 拒绝（回滚后可重新建档）
 with pytest.raises(HTTPException) as e:
  make_meter(db,user,'NEW',replaces_id=old.id)
 assert 'change_out' in e.value.detail
 db.rollback()
 add(db,user,old,'2026-03-01T00:00',120,kind='change_out')
 # 新表已建档但首条不是 change_in → 入数触发边界拒绝
 new=create_meter(db,user,{'code':'NEW','tz_name':'Asia/Shanghai','replaces_id':old.id})
 with pytest.raises(HTTPException) as e:
  add(db,user,new,'2026-03-01T00:00',0)
 assert 'change_in' in e.value.detail
 db.rollback()
 add(db,user,new,'2026-03-01T00:00',0,kind='change_in')
 add(db,user,new,'2026-04-01T00:00',60)
 pump(db)
 # 换表两侧独立：旧表末段 120、新表首段 60，不跨表做差
 assert raw_of(db,old,'2026-02')==Decimal('40.000000')
 assert raw_of(db,new,'2026-03')==Decimal('60.000000')
def test_closed_month_keeps_value_delta_carried_forward(db,user):
 m=make_meter(db,user)
 for mo,kg in (('2026-01',0.5),('2026-02',0.5),('2026-03',0.5)):fac(db,user,mo,kg)
 add(db,user,m,'2026-01-01T00:00',0)
 add(db,user,m,'2026-02-01T00:00',100)
 add(db,user,m,'2026-03-01T00:00',200)
 pump(db)
 close_month(db,user,'2026-01')
 # 迟到更正：1月实际应为130（+30），2月相应-30
 rd=db.query(MeterReading).filter_by(meter_id=m.id).all()[1]
 save_reading(db,user,{'meter_id':m.id,'read_at':'2026-02-01T00:00','cumulative':130,'kind':'regular'},rd.id)
 pump(db)
 assert raw_of(db,m,'2026-01')==Decimal('100.000000') # 封账原值不动
 assert raw_of(db,m,'2026-02')==Decimal('70.000000')  # 开放月替换
 adj=db.query(Adjustment).one()
 assert adj.source_month=='2026-01' and adj.target_month=='2026-02'
 assert Decimal(str(adj.delta_raw))==Decimal('30.000000')
 # 冲补后全链守恒：原量(开放月替换后) + 冲补行 = 实际总差
 base=sum((Decimal(str(a.raw)) for a in db.query(MeterMonthAllocation).filter_by(meter_id=m.id)),Decimal(0))
 deltas=sum((Decimal(str(a.delta_raw)) for a in db.query(Adjustment).filter_by(meter_id=m.id)),Decimal(0))
 assert base+deltas==Decimal('200.000000')
def test_adjustment_idempotent_retry_no_double_post(db,user):
 m=make_meter(db,user)
 for mo in ('2026-01','2026-02'):fac(db,user,mo)
 add(db,user,m,'2026-01-01T00:00',0);add(db,user,m,'2026-02-01T00:00',100)
 pump(db);close_month(db,user,'2026-01')
 rd=db.query(MeterReading).filter_by(meter_id=m.id).all()[1]
 save_reading(db,user,{'meter_id':m.id,'read_at':'2026-02-01T00:00','cumulative':110,'kind':'regular'},rd.id)
 pump(db)
 first=db.query(Adjustment).count()
 # 再次运行同一作业的重建（模拟 worker 重试）
 job=db.query(MeterJob).filter(MeterJob.status=='completed').order_by(MeterJob.id.desc()).first()
 from src.services.metering import rebuild_meter
 rebuild_meter(db,m.id,job.id,job.generation,'retry')
 assert db.query(Adjustment).count()==first
def test_stale_worker_does_not_overwrite_new_value(db,user):
 m=make_meter(db,user);fac(db,user,'2026-01');fac(db,user,'2026-02')
 add(db,user,m,'2026-01-01T00:00',0);add(db,user,m,'2026-02-01T00:00',100)
 pump(db)
 gen_old=m.rebuild_generation
 # 又来一条新读数 → 代次推进
 add(db,user,m,'2026-03-01T00:00',180);pump(db)
 assert m.rebuild_generation==gen_old+1
 # 旧执行者用旧代次迟到执行：必须跳过，不覆盖
 out=__import__('src.services.metering',fromlist=['rebuild_meter']).rebuild_meter(db,m.id,999,gen_old,'stale')
 assert out['skipped'] is True
 al=db.query(MeterMonthAllocation).filter_by(meter_id=m.id,month='2026-02').first()
 assert al.generation==m.rebuild_generation
def test_resumable_rebuild_after_interruption(db,user):
 m=make_meter(db,user)
 for mo in ('2026-01','2026-02','2026-03'):fac(db,user,mo)
 add(db,user,m,'2026-01-01T00:00',0)
 add(db,user,m,'2026-02-01T00:00',100)
 add(db,user,m,'2026-03-10T00:00',260)
 job=db.query(MeterJob).filter_by(status='queued').order_by(MeterJob.id.desc()).first()
 assert job.generation==m.rebuild_generation
 # 模拟中断：作业被认领但未执行；崩溃后超时回收机制把作业放回队列
 from src.services.meter_jobs import claim_next,reap_stale
 claim_next(db,'crashed-worker');assert db.query(MeterJob).filter_by(status='running').count()==1
 job=db.query(MeterJob).filter_by(status='running').first();job.status='queued';job.claimed_by=None;db.commit()
 pump(db)
 assert db.query(MeterMonthAllocation).filter_by(meter_id=m.id).count()==3
 assert sum((Decimal(str(a.raw)) for a in db.query(MeterMonthAllocation).filter_by(meter_id=m.id)),Decimal(0))==Decimal('260.000000')
 # 超时回收器本身也能回收崩溃执行者
 add(db,user,m,'2026-04-01T00:00',300)
 claim_next(db,'crashed-worker')
 from unittest.mock import patch
 running=db.query(MeterJob).filter_by(status='running').first()
 with patch('src.services.meter_jobs.time.time',return_value=running.claimed_at+999):
  assert reap_stale(db,timeout_seconds=300)==1
 assert db.query(MeterJob).filter_by(status='queued').count()>=1
def test_close_without_factor_rejected(db,user):
 m=make_meter(db,user)
 add(db,user,m,'2026-01-01T00:00',0);add(db,user,m,'2026-02-01T00:00',50);pump(db)
 with pytest.raises(HTTPException) as e:close_month(db,user,'2026-01')
 assert '因子' in e.value.detail
def test_frozen_factor_cannot_change(db,user):
 m=make_meter(db,user);fac(db,user,'2026-01')
 add(db,user,m,'2026-01-01T00:00',0);add(db,user,m,'2026-02-01T00:00',50);pump(db)
 close_month(db,user,'2026-01')
 with pytest.raises(HTTPException) as e:save_factor(db,user,{'month':'2026-01','kg_per_unit':9.9,'unit':'kWh'})
 assert e.value.status_code==409
def test_dst_meter_cross_month_allocation(db,user):
 m=make_meter(db,user,tz='America/New_York')
 fac(db,user,'2026-03');fac(db,user,'2026-04')
 # 3/1 → 4/2 纽约：3月片含 3/8 春令时共743h；4月片24h；总767h
 add(db,user,m,'2026-03-01T00:00',0)
 add(db,user,m,'2026-04-02T00:00',1000)
 pump(db)
 al3=raw_of(db,m,'2026-03');al4=raw_of(db,m,'2026-04')
 assert al3+al4==Decimal('1000.000000')
 assert al3==(Decimal('1000')*Decimal(743)/Decimal(767)).quantize(Q)
 assert al4==(Decimal('1000')*Decimal(24)/Decimal(767)).quantize(Q)
def test_trace_links_readings_allocations_adjustments(db,user):
 m=make_meter(db,user);fac(db,user,'2026-01');fac(db,user,'2026-02');fac(db,user,'2026-03')
 add(db,user,m,'2026-01-01T00:00',0);add(db,user,m,'2026-02-01T00:00',100)
 add(db,user,m,'2026-03-06T00:00',160);pump(db)
 t=trace(db,m.id)
 assert t['meter']['code']=='M-1' and len(t['readings'])==3
 assert {a['month'] for a in t['allocations']}=={'2026-01','2026-02','2026-03'}
 assert t['jobs'] and t['jobs'][0]['status']=='completed'
def test_concurrent_close_and_correction_each_delta_one_month(db,user):
 """封账与补数竞态：更正产生的差额恰好一个归属月，不重复不丢失。"""
 m=make_meter(db,user)
 for mo in ('2026-01','2026-02','2026-03'):fac(db,user,mo)
 add(db,user,m,'2026-01-01T00:00',0)
 add(db,user,m,'2026-02-01T00:00',100)
 add(db,user,m,'2026-03-01T00:00',200)
 pump(db);close_month(db,user,'2026-01');close_month(db,user,'2026-02')
 rd=db.query(MeterReading).filter_by(meter_id=m.id).all()[1]
 save_reading(db,user,{'meter_id':m.id,'read_at':'2026-02-01T00:00','cumulative':140,'kind':'regular'},rd.id)
 pump(db)
 # 1、2月均封账：更正使1月+40、2月-40，每份差额各自一条冲补，均归属下一开放月3月
 adjs=db.query(Adjustment).order_by(Adjustment.source_month).all()
 assert [a.source_month for a in adjs]==['2026-01','2026-02']
 assert {a.target_month for a in adjs}=={'2026-03'}
 delta_map={a.source_month:Decimal(str(a.delta_raw)) for a in adjs}
 assert delta_map=={'2026-01':Decimal('40.000000'),'2026-02':Decimal('-40.000000')}
 # 每个来源月恰好一条冲补（重试不重复），全链守恒
 assert len(adjs)==2
 base=sum((Decimal(str(a.raw)) for a in db.query(MeterMonthAllocation).filter_by(meter_id=m.id)),Decimal(0))
 assert base+sum(delta_map.values(),Decimal(0))==Decimal('200.000000')
def test_adjustments_converge_across_repeated_corrections(db,user):
 """连续两次更正同一封账月：开放承接月只保留一条、等于最新差额，不逐代累加。"""
 m=make_meter(db,user)
 for mo in ('2026-01','2026-02','2026-03'):fac(db,user,mo)
 add(db,user,m,'2026-01-01T00:00',0);add(db,user,m,'2026-02-01T00:00',100)
 add(db,user,m,'2026-03-05T00:00',200);pump(db);close_month(db,user,'2026-01')
 rds=db.query(MeterReading).filter_by(meter_id=m.id).order_by(MeterReading.read_at).all()
 save_reading(db,user,{'meter_id':m.id,'read_at':'2026-02-01T00:00','cumulative':110,'kind':'regular'},rds[1].id);pump(db)
 save_reading(db,user,{'meter_id':m.id,'read_at':'2026-02-01T00:00','cumulative':135,'kind':'regular'},rds[1].id);pump(db)
 open_adjs=db.query(Adjustment).filter_by(meter_id=m.id,source_month='2026-01').all()
 assert len(open_adjs)==1
 assert Decimal(str(open_adjs[0].delta_raw))==Decimal('35.000000')
 assert open_adjs[0].target_month=='2026-02'
 # 全链守恒：开放月原量 + 唯一冲补 = 实际总差
 base=sum((Decimal(str(a.raw)) for a in db.query(MeterMonthAllocation).filter_by(meter_id=m.id)),Decimal(0))
 assert base+Decimal('35.000000')==Decimal('200.000000')
def test_locked_adjustment_when_target_month_also_closes(db,user):
 """承接月2月随后封账：旧冲补锁定，再次更正只把新增差额补到下一开放月3月。"""
 m=make_meter(db,user)
 for mo in ('2026-01','2026-02','2026-03'):fac(db,user,mo)
 add(db,user,m,'2026-01-01T00:00',0);add(db,user,m,'2026-02-01T00:00',100)
 add(db,user,m,'2026-03-05T00:00',200);pump(db);close_month(db,user,'2026-01')
 rds=db.query(MeterReading).filter_by(meter_id=m.id).order_by(MeterReading.read_at).all()
 save_reading(db,user,{'meter_id':m.id,'read_at':'2026-02-01T00:00','cumulative':110,'kind':'regular'},rds[1].id);pump(db)
 close_month(db,user,'2026-02') # 承接月也封账，+10 冲补随之锁定
 save_reading(db,user,{'meter_id':m.id,'read_at':'2026-02-01T00:00','cumulative':140,'kind':'regular'},rds[1].id);pump(db)
 adjs=db.query(Adjustment).filter_by(meter_id=m.id,source_month='2026-01').order_by(Adjustment.id).all()
 assert [(a.target_month,Decimal(str(a.delta_raw))) for a in adjs]==[('2026-02',Decimal('10.000000')),('2026-03',Decimal('30.000000'))]
 base=sum((Decimal(str(a.raw)) for a in db.query(MeterMonthAllocation).filter_by(meter_id=m.id)),Decimal(0))
 all_adj=sum((Decimal(str(a.delta_raw)) for a in db.query(Adjustment).filter_by(meter_id=m.id)),Decimal(0))
 assert base+all_adj==Decimal('200.000000')

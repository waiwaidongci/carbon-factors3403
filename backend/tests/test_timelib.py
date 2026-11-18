"""纯函数：时区月界、夏令时、跨年时长分摊、守恒。"""
from datetime import datetime,timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from src.services.timelib import parse_instant,split_segment,allocate_delta,split_allocation,month_bounds,QUANT
SHA=ZoneInfo('Asia/Shanghai');NY=ZoneInfo('America/New_York')
def D(y,mo,d,h=0,tz=SHA):return datetime(y,mo,d,h,tzinfo=tz).astimezone(timezone.utc)
def test_month_bounds_shanghai():
 s,e=month_bounds(D(2026,2,15,12), 'Asia/Shanghai')
 assert s==datetime(2026,2,1,tzinfo=SHA).astimezone(timezone.utc)
 assert e==datetime(2026,3,1,tzinfo=SHA).astimezone(timezone.utc)
def test_split_across_two_months_real_duration():
 # 1/31 20:00 → 2/1 04:00 上海：4h 在1月，4h 在2月（UTC秒一致）
 pieces=split_segment(D(2026,1,31,20),D(2026,2,1,4),'Asia/Shanghai')
 assert [m for m,_ in pieces]==['2026-01','2026-02']
 assert sum((s for _,s in pieces),Decimal(0))==Decimal(28800)
def test_split_cross_year():
 pieces=split_segment(D(2026,12,31,20),D(2027,1,1,8),'Asia/Shanghai')
 assert [m for m,_ in pieces]==['2026-12','2027-01']
def test_dst_spring_forward_23_hour_day():
 # 纽约 2026-03-08 春令时：02:00→03:00 跳过，当天只有23小时
 pieces=split_segment(datetime(2026,3,8,0,tzinfo=NY).astimezone(timezone.utc),
                      datetime(2026,3,9,0,tzinfo=NY).astimezone(timezone.utc),'America/New_York')
 total=sum((s for _,s in pieces),Decimal(0))
 assert total==Decimal(23*3600)
def test_dst_fall_back_25_hour_day():
 # 纽约 2026-11-01 回落：当天25小时
 pieces=split_segment(datetime(2026,11,1,0,tzinfo=NY).astimezone(timezone.utc),
                      datetime(2026,11,2,0,tzinfo=NY).astimezone(timezone.utc),'America/New_York')
 total=sum((s for _,s in pieces),Decimal(0))
 assert total==Decimal(25*3600)
def test_dst_segment_split_weights():
 # 春令时当天 23h 内均匀用电，3月片与2月片按真实秒数加权
 start=datetime(2026,2,28,0,tzinfo=NY).astimezone(timezone.utc)
 end=datetime(2026,3,8,0,tzinfo=NY).astimezone(timezone.utc)
 pieces=split_segment(start,end,'America/New_York')
 shares=dict(split_allocation(start,end,Decimal('1000'),'America/New_York'))
 assert sum(shares.values(),Decimal(0))==Decimal('1000')
 # 区间 2/28→3/8 零点：2月片仅 2/28→3/1 共24h；3月片 3/1→3/8 共7天168h
 assert shares['2026-02']==(Decimal(1000)*Decimal(24)/Decimal(192)).quantize(QUANT)
 assert shares['2026-03']==(Decimal(1000)*Decimal(168)/Decimal(192)).quantize(QUANT)
def test_allocation_conservation_with_rounding():
 pieces=[('2026-01',Decimal(10)),('2026-02',Decimal(20)),('2026-03',Decimal(33))]
 for delta in (Decimal('100'),Decimal('0.000001'),Decimal('123.456789'),Decimal('999999.000001')):
  shares=allocate_delta(delta,pieces)
  got=sum((v for _,v in shares),Decimal(0))
  assert got==delta.quantize(QUANT)
  assert all(v>=0 for _,v in shares)
def test_single_piece_returns_full_delta():
 assert allocate_delta(Decimal('55.5'),[('2026-01',Decimal(100))])==[('2026-01',Decimal('55.5'))]
def test_parse_wall_time_carries_offset_snapshot():
 inst=parse_instant('2026-03-08T12:00','America/New_York')
 loc=inst.astimezone(NY)
 assert loc.utcoffset().total_seconds()==-4*3600 # 3/8 已进入EDT
 inst2=parse_instant('2026-11-01T12:00','America/New_York')
 assert inst2.astimezone(NY).utcoffset().total_seconds()==-5*3600 # 11/1 回落为EST
def test_non_increasing_time_rejected():
 import pytest
 with pytest.raises(ValueError):
  split_segment(D(2026,1,2),D(2026,1,1),'Asia/Shanghai')
def test_invalid_timezone_rejected():
 import pytest
 with pytest.raises(ValueError):
  parse_instant('2026-01-01T00:00','Mars/Olympus')

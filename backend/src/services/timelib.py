"""时区月界与跨月时长分摊的纯函数。

所有时刻以带时区的 datetime 表达；时长一律用 UTC 秒数计算，
因此夏令时切换日的 23/25 小时会被自然计入，无需手工偏移表。
月键统一为 'YYYY-MM'。
"""
from datetime import datetime,timezone
from decimal import Decimal,ROUND_HALF_UP
from zoneinfo import ZoneInfo
QUANT=Decimal('0.000001')
def zone(tz_name):
 try:
  return ZoneInfo(tz_name)
 except Exception as exc:
  raise ValueError('时区不合法：'+str(tz_name)) from exc
def month_key(dt):return '%04d-%02d'%(dt.year,dt.month)
def parse_instant(text,tz_name):
 """接受 'YYYY-MM-DDTHH:MM[:SS]' 本地墙钟时间或带偏移的 ISO 串，返回 UTC 感知时刻。"""
 if isinstance(text,datetime):
  dt=text
  if dt.tzinfo is None:
   dt=dt.replace(tzinfo=zone(tz_name))
  return dt.astimezone(timezone.utc)
 if not isinstance(text,str) or not text.strip():raise ValueError('时刻不能为空')
 s=text.strip().replace(' ','T')
 try:
  dt=datetime.fromisoformat(s)
 except ValueError as exc:
  raise ValueError('时刻格式不合法，应为YYYY-MM-DDTHH:MM:SS') from exc
 if dt.tzinfo is None:dt=dt.replace(tzinfo=zone(tz_name))
 return dt.astimezone(timezone.utc)
def local_of(instant,tz_name):return instant.astimezone(zone(tz_name))
def next_month_start_local(dt_local):
 y,m=dt_local.year,dt_local.month
 if m==12:return datetime(y+1,1,1,tzinfo=dt_local.tzinfo)
 return datetime(y,m+1,1,tzinfo=dt_local.tzinfo)
def month_bounds(instant,tz_name):
 """返回该 UTC 时刻在表时区所属月的本地起止，转回 UTC 感知时刻。"""
 loc=instant.astimezone(zone(tz_name))
 start=datetime(loc.year,loc.month,1,tzinfo=loc.tzinfo)
 return start.astimezone(timezone.utc),next_month_start_local(start).astimezone(timezone.utc)
def split_segment(start_utc,end_utc,tz_name):
 """把 [start,end) 按表时区月界切片，返回 [(month, 真实秒数)]，秒为 Decimal。"""
 if end_utc<=start_utc:raise ValueError('读数时刻必须严格递增')
 pieces=[];cur=start_utc
 while cur<end_utc:
  m_start,m_end=month_bounds(cur,tz_name)
  seg_end=min(end_utc,m_end)
  seconds=Decimal(str((seg_end-cur).total_seconds()))
  if seconds>0:pieces.append((month_key(cur.astimezone(zone(tz_name))),seconds))
  cur=seg_end
 return pieces
def allocate_delta(delta,pieces,quant=QUANT):
 """按真实时长占比把累计读数差拆到各月。

 先按 quant 截断取整，再把全部余量（不超过一个 quant）加给时长最大的片，
 保证各月之和严格等于原差；单片直接返回原差。
 """
 delta=Decimal(str(delta))
 if not pieces:return []
 total_seconds=sum((s for _,s in pieces),Decimal(0))
 if total_seconds<=0:raise ValueError('时长必须为正')
 if len(pieces)==1:return [(pieces[0][0],delta)]
 shares=[]
 for month,seconds in pieces:
  raw=delta*seconds/total_seconds
  shares.append([month,raw.quantize(quant,rounding=ROUND_HALF_UP)])
 remainder=delta-sum((v for _,v in shares),Decimal(0))
 if remainder:
  idx=max(range(len(pieces)),key=lambda i:pieces[i][1])
  shares[idx][1]+=remainder
 got=sum((v for _,v in shares),Decimal(0))
 assert got==delta,(got,delta)
 return shares
def split_allocation(start_utc,end_utc,delta,tz_name):
 return allocate_delta(Decimal(str(delta)),split_segment(start_utc,end_utc,tz_name))
def add_months(month,n):
 y,m=int(month[:4]),int(month[5:7]);x=y*12+(m-1)+n;return '%04d-%02d'%(x//12,x%12+1)
def now_month(tz_name=None):
 dt=datetime.now(zone(tz_name)) if tz_name else datetime.now(timezone.utc)
 return month_key(dt)

from sqlalchemy import Column,Integer,String,JSON,Text,Float,Boolean,DateTime,Numeric,ForeignKey,UniqueConstraint
from sqlalchemy.types import TypeDecorator
from datetime import timezone
from src.config.database import Base
class UTCDateTime(TypeDecorator):
 """落库 UTC；读出统一补回 UTC 时区，兼容 SQLite(naive) 与 PG(timestamptz)。"""
 impl=DateTime(timezone=True);cache_ok=True
 def process_bind_param(self,value,dialect):
  if value is None:return None
  if value.tzinfo is None:raise ValueError('时刻必须带时区')
  return value.astimezone(timezone.utc)
 def process_result_value(self,value,dialect):
  if value is None:return None
  return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
class Meter(Base):
 __tablename__='meters'
 id=Column(Integer,primary_key=True); code=Column(String(60),unique=True,nullable=False); name=Column(String(160)); tz_name=Column(String(80),nullable=False); unit=Column(String(20),default='kWh'); replaces_id=Column(Integer,ForeignKey('meters.id')); rebuild_generation=Column(Integer,default=0,nullable=False); created=Column(Float)
class MeterReading(Base):
 __tablename__='meter_readings'
 id=Column(Integer,primary_key=True); meter_id=Column(Integer,ForeignKey('meters.id'),nullable=False)
 read_at=Column(UTCDateTime,nullable=False); tz_name=Column(String(80),nullable=False); utc_offset_min=Column(Integer,nullable=False)
 cumulative=Column(Numeric(18,6),nullable=False); kind=Column(String(20),default='regular',nullable=False); note=Column(String(250),default='')
 recorded_at=Column(Float); updated_at=Column(Float)
 __table_args__=(UniqueConstraint('meter_id','read_at',name='uq_meter_read_at'),)
class MonthFactor(Base):
 __tablename__='month_factors'
 id=Column(Integer,primary_key=True); month=Column(String(7),unique=True,nullable=False); unit=Column(String(20),default='kWh'); kg_per_unit=Column(Numeric(18,6),nullable=False); frozen=Column(Boolean,default=False,nullable=False); frozen_by=Column(String(120)); frozen_at=Column(Float); updated_at=Column(Float)
class MonthClose(Base):
 __tablename__='month_closes'
 month=Column(String(7),primary_key=True); closed=Column(Boolean,default=True,nullable=False); closed_by=Column(String(120)); closed_at=Column(Float)
class MeterMonthAllocation(Base):
 __tablename__='meter_month_allocations'
 id=Column(Integer,primary_key=True); meter_id=Column(Integer,ForeignKey('meters.id'),nullable=False); month=Column(String(7),nullable=False)
 raw=Column(Numeric(18,6),nullable=False); factor=Column(Numeric(18,6)); emissions=Column(Numeric(18,6)); generation=Column(Integer,default=0,nullable=False); job_id=Column(Integer); updated_at=Column(Float)
 __table_args__=(UniqueConstraint('meter_id','month',name='uq_meter_month'),)
class Adjustment(Base):
 __tablename__='allocation_adjustments'
 id=Column(Integer,primary_key=True); idem_key=Column(String(180),unique=True,nullable=False); meter_id=Column(Integer,ForeignKey('meters.id'),nullable=False)
 source_month=Column(String(7),nullable=False); target_month=Column(String(7),nullable=False); delta_raw=Column(Numeric(18,6),nullable=False); delta_emissions=Column(Numeric(18,6),nullable=False)
 reason=Column(String(250)); job_id=Column(Integer); generation=Column(Integer,default=0,nullable=False); created_at=Column(Float)
class MeterJob(Base):
 __tablename__='meter_jobs'
 id=Column(Integer,primary_key=True); kind=Column(String(20),nullable=False); status=Column(String(20),default='queued',nullable=False); generation=Column(Integer,default=0,nullable=False); meter_id=Column(Integer,ForeignKey('meters.id'))
 payload=Column(JSON,default=dict); result=Column(JSON); error=Column(Text); claimed_by=Column(String(80)); version=Column(Integer,default=0,nullable=False); attempts=Column(Integer,default=0,nullable=False)
 created=Column(Float); claimed_at=Column(Float); finished=Column(Float)

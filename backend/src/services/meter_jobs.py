"""抄表重算作业队列。

- 入数/更正只影响对应表：同表同类作业合并到最新代次，排队中作业复用。
- 认领用行锁（PG: SKIP LOCKED）保证一份作业只被一个执行者领取。
- 作业完成时再次校验代次；旧执行者晚到不覆盖新值。
- 引擎按检查点逐步提交，进程中断后下一轮重新认领即可恢复。
"""
import time,traceback
from src.models.metering import Meter,MeterJob
from src.config.settings import REDIS_URL
from src.services.metering import rebuild_meter
import redis
def enqueue(db,meter,kind='rebuild',payload=None,actor='system'):
 meter.rebuild_generation=(meter.rebuild_generation or 0)+1;db.flush()
 pending=db.query(MeterJob).filter(MeterJob.meter_id==meter.id,MeterJob.kind==kind,MeterJob.status=='queued').first()
 if pending:
  pending.generation=meter.rebuild_generation;pending.payload=payload or pending.payload;pending.version+=1
  db.commit();return pending
 job=MeterJob(kind=kind,status='queued',generation=meter.rebuild_generation,meter_id=meter.id,
  payload=payload or {},version=0,created=time.time())
 db.add(job);db.commit()
 try:redis.Redis.from_url(REDIS_URL).rpush('meter-jobs',job.id)
 except Exception:pass
 return job
def claim_next(db,worker_id,exclude=None):
 q=db.query(MeterJob).filter(MeterJob.status=='queued')
 if exclude:q=q.filter(MeterJob.id.notin_(exclude))
 q=q.order_by(MeterJob.id)
 try:pending=q.with_for_update(skip_locked=True).first()
 except TypeError:pending=q.with_for_update().first()
 if not pending:return None
 pending.status='running';pending.claimed_by=worker_id;pending.claimed_at=time.time()
 pending.attempts=(pending.attempts or 0)+1;pending.version+=1
 db.commit();return pending
def execute(db,job,worker_id):
 meter=db.get(Meter,job.meter_id) if job.meter_id else None
 if meter and meter.rebuild_generation>job.generation:
  job.status='completed';job.result={'skipped':True,'reason':'superseded_by_newer_job'}
  job.finished=time.time();db.commit();return
 try:
  if job.kind=='rebuild':
   result=rebuild_meter(db,job.meter_id,job.id,job.generation,actor=worker_id)
  else:
   result={'skipped':True,'reason':'unknown_kind'}
  job=db.get(MeterJob,job.id)
  meter=db.get(Meter,job.meter_id) if job.meter_id else None
  if meter and meter.rebuild_generation>job.generation:
   job.status='failed';job.error='作业代次已过期，新核算已由后续作业接管';job.finished=time.time()
  else:
   job.status='completed';job.result=result;job.finished=time.time()
  db.commit()
 except Exception as exc:
  db.rollback()
  job=db.get(MeterJob,job.id)
  if job:
   if (job.attempts or 0)>=10:
    job.status='failed';job.error='重试10次仍失败，转死信：%s'%exc;job.finished=time.time()
   else:
    job.status='queued';job.error=str(exc);job.claimed_by=None;job.claimed_at=None
    job.version+=1
   db.commit()
  traceback.print_exc()
def reap_stale(db,timeout_seconds=300):
 """崩溃执行者遗留的 running 作业超时回收，保证重算中断可恢复。"""
 cutoff=time.time()-timeout_seconds
 stale=db.query(MeterJob).filter(MeterJob.status=='running',MeterJob.claimed_at<cutoff).all()
 for j in stale:
  j.status='queued';j.claimed_by=None;j.claimed_at=None;j.version+=1
  j.error=(j.error or '')+'；检测到执行者超时，回收重试'
 if stale:db.commit()
 return len(stale)
def pump(db,worker_id='worker'):
 """每轮尽量排空队列；本轮已失败回队的作业留给下一轮，避免毒消息空转。"""
 processed=0;attempted=set()
 while True:
  job=claim_next(db,worker_id,exclude=attempted)
  if not job:break
  attempted.add(job.id)
  execute(db,job,worker_id);processed+=1
 return processed

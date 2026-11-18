import time,traceback
from pathlib import Path
import redis
from src.config.database import Base,engine,Session
from src.config.settings import REDIS_URL
from src.models import identity,source,run,metering
from src.models.run import Run
from src.services.calculation import calculate
from src.services.delivery import tick
from src.services.meter_jobs import pump,reap_stale
Base.metadata.create_all(engine)
def redis_client():
 try:return redis.Redis.from_url(REDIS_URL)
 except Exception:return None
def lpop(name):
 try:return redis_client().lpop(name)
 except Exception:return None
while True:
 Path('/tmp/worker-heartbeat').write_text(str(time.time()))
 try:
  item=lpop('report-jobs')
  if item:
   with Session() as db:
    job=db.get(Run,int(item))
    if job and job.status=='queued':
     job.status='running';db.commit()
     try: job.result=calculate(job.snapshot['sources'],job.snapshot['policy']);job.status='completed'
     except Exception as exc: job.status='failed';job.error=str(exc);traceback.print_exc()
     db.commit()
  # 抄表队列：Redis 仅作唤醒；DB 轮询兜底，保证漏通知/中断后仍可恢复
  lpop('meter-jobs')
  with Session() as db:
   reap_stale(db)
   pump(db)
   tick(db)
 except Exception: traceback.print_exc()
 time.sleep(1)

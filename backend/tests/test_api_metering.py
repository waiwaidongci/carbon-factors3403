"""HTTP 级：权限、端到端入数→异步重算→封账→冲补、拒绝路径。"""
import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.config import database as db_mod
from src.models import identity,source,run,metering
from src.utils.security import password_hash
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
@pytest.fixture()
def client():
 engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
 db_mod.engine=engine;db_mod.Session=sessionmaker(bind=engine,expire_on_commit=False)
 for b in (identity.Base,source.Base,run.Base,metering.Base):b.metadata.create_all(engine)
 with db_mod.Session() as db:
  for role in ('admin','analyst','viewer'):
   db.add(identity.User(email=role+'@demo.local',role=role,password=password_hash('Demo2026!')))
  db.commit()
 with TestClient(app) as c:yield c
def token(c,role):
 r=c.post('/auth/login',json={'email':role+'@demo.local','password':'Demo2026!'})
 assert r.status_code==200
 return {'Authorization':'Bearer '+r.json()['token']}
def test_end_to_end_metering_flow_and_permissions(client):
 admin=token(client,'admin');analyst=token(client,'analyst');viewer=token(client,'viewer')
 # viewer 只读
 r=client.post('/meters',headers=viewer,json={'code':'E-1','tz_name':'Asia/Shanghai'})
 assert r.status_code==403
 r=client.post('/meters',headers=analyst,json={'code':'E-1','tz_name':'Asia/Shanghai','unit':'kWh'})
 assert r.status_code==200;mid=r.json()['id']
 # 非法时区拒绝
 r=client.post('/meters',headers=analyst,json={'code':'BAD','tz_name':'Moon/Sea'})
 assert r.status_code in (422,400)
 for mo,kg in (('2026-01',0.5),('2026-02',0.6)):
  assert client.post('/month-factors',headers=analyst,json={'month':mo,'kg_per_unit':kg,'unit':'kWh'}).status_code==200
 for ts,cum in (('2026-01-01T00:00',0),('2026-02-01T00:00',100),('2026-02-20T00:00',180)):
  r=client.post('/readings',headers=analyst,json={'meter_id':mid,'read_at':ts,'cumulative':cum})
  assert r.status_code==200
 # 累计倒退拒绝
 r=client.post('/readings',headers=analyst,json={'meter_id':mid,'read_at':'2026-02-25T00:00','cumulative':170})
 assert r.status_code==422 and '倒退' in r.json()['detail']
 # 同步驱动队列（测试环境无 worker）
 assert client.post('/meter-jobs/pump',headers=admin).json()['processed']>=1
 allocs=client.get('/allocations',headers=viewer,params={'meter_id':mid}).json()
 months={a['month']:a for a in allocs}
 assert set(months)=={'2026-01','2026-02'}
 assert sum(float(a['raw']) for a in allocs)==180.0
 # analyst 不能封账
 assert client.post('/month-closes',headers=analyst,json={'month':'2026-01'}).status_code==403
 assert client.post('/month-closes',headers=admin,json={'month':'2026-01'}).status_code==200
 # 迟到更正：1月 +20，封账月不动，差额冲补到2月
 rd=[x for x in client.get('/readings',headers=viewer).json() if x['meter_id']==mid][1]
 r=client.put('/readings/'+str(rd['id']),headers=analyst,
  json={'meter_id':mid,'read_at':'2026-02-01T00:00','cumulative':120})
 assert r.status_code==200
 client.post('/meter-jobs/pump',headers=admin)
 allocs=client.get('/allocations',headers=viewer,params={'meter_id':mid}).json()
 jan=[a for a in allocs if a['month']=='2026-01'][0]
 assert float(jan['raw'])==100.0 # 封账原值保留
 adjs=client.get('/adjustments',headers=viewer).json()
 assert len(adjs)==1 and adjs[0]['source_month']=='2026-01' and adjs[0]['target_month']=='2026-02'
 assert float(adjs[0]['delta_raw'])==20.0
 # 幂等：再 pump 不产生新冲补
 client.post('/meter-jobs/pump',headers=admin)
 assert len(client.get('/adjustments',headers=viewer).json())==1
 # 追溯页数据齐全
 t=client.get('/meters/%s/trace'%mid,headers=viewer).json()
 assert len(t['readings'])==3 and t['closed_months']==['2026-01']
 assert t['adjustments'][0]['idem_key']
 # 冻结因子不可改
 r=client.post('/month-factors',headers=analyst,json={'month':'2026-01','kg_per_unit':9.9})
 assert r.status_code==409
def test_meter_change_missing_boundary_rejected(client):
 admin=token(client,'admin')
 m1=client.post('/meters',headers=admin,json={'code':'OLD','tz_name':'America/New_York'}).json()
 client.post('/readings',headers=admin,json={'meter_id':m1['id'],'read_at':'2026-01-01T00:00','cumulative':0})
 # 旧表无 change_out，新表建档拒绝
 r=client.post('/meters',headers=admin,json={'code':'NEW','tz_name':'America/New_York','replaces_id':m1['id']})
 assert r.status_code==422 and 'change_out' in r.json()['detail']

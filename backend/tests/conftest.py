import os,sys,types,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('DATABASE_URL','sqlite://')
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('REDIS_URL','redis://127.0.0.1:6390/15')
# settings.py 硬编码 /app/project.json，测试环境用装载后的假模块替代
fake=types.ModuleType('src.config.settings')
fake.CONFIG=json.loads((ROOT/'project.json').read_text())
fake.DATABASE_URL='sqlite://'
fake.REDIS_URL=os.environ['REDIS_URL']
fake.SECRET='test-secret'
sys.modules.setdefault('src.config.settings',fake)
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.config import database as db_mod
from src.models import identity,source,run,metering
@pytest.fixture()
def db():
 engine=create_engine('sqlite://',connect_args={'check_same_thread':False})
 db_mod.engine=engine;db_mod.Session=sessionmaker(bind=engine,expire_on_commit=False)
 identity.Base.metadata.create_all(engine);source.Base.metadata.create_all(engine)
 run.Base.metadata.create_all(engine);metering.Base.metadata.create_all(engine)
 s=db_mod.Session();yield s;s.close()
class U:
 def __init__(self,email='admin@demo.local'):self.email=email
@pytest.fixture()
def user():return U()

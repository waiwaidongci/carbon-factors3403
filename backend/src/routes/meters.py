from fastapi import APIRouter,Depends
from src.config.database import database
from src.controllers.auth import current,editor,admin
from src.models.metering import Meter,MeterReading,MonthFactor,MonthClose,MeterMonthAllocation,Adjustment,MeterJob
from src.schemas.requests import MeterInput,ReadingInput,FactorInput,MonthInput
from src.services.meter_app import create_meter,save_reading,delete_reading,save_factor,close_month,reopen_month,trace
from src.services.meter_jobs import pump
from src.utils.serialize import row
router=APIRouter()
@router.get('/meters')
def meters(db=Depends(database),u=Depends(current)):return [row(x) for x in db.query(Meter).order_by(Meter.id).all()]
@router.post('/meters')
def add_meter(b:MeterInput,db=Depends(database),u=Depends(editor)):return row(create_meter(db,u,b.model_dump()))
@router.put('/meters/{id}')
def edit_meter(id:int,b:MeterInput,db=Depends(database),u=Depends(editor)):return row(create_meter(db,u,b.model_dump(),id))
@router.get('/meters/{id}/trace')
def meter_trace(id:int,db=Depends(database),u=Depends(current)):return trace(db,id)
@router.get('/readings')
def readings(db=Depends(database),u=Depends(current),meter_id:int|None=None):
 q=db.query(MeterReading).order_by(MeterReading.meter_id,MeterReading.read_at)
 if meter_id:q=q.filter(MeterReading.meter_id==meter_id)
 return [row(x) for x in q.all()]
@router.post('/readings')
def add_reading(b:ReadingInput,db=Depends(database),u=Depends(editor)):
 obj,job=save_reading(db,u,b.model_dump());return {'reading':row(obj),'job':row(job)}
@router.put('/readings/{id}')
def edit_reading(id:int,b:ReadingInput,db=Depends(database),u=Depends(editor)):
 obj,job=save_reading(db,u,b.model_dump(),id);return {'reading':row(obj),'job':row(job)}
@router.delete('/readings/{id}')
def remove_reading(id:int,db=Depends(database),u=Depends(editor)):return row(delete_reading(db,u,id))
@router.get('/month-factors')
def factors(db=Depends(database),u=Depends(current)):return [row(x) for x in db.query(MonthFactor).order_by(MonthFactor.month).all()]
@router.post('/month-factors')
def upsert_factor(b:FactorInput,db=Depends(database),u=Depends(editor)):return row(save_factor(db,u,b.model_dump()))
@router.get('/month-closes')
def closes(db=Depends(database),u=Depends(current)):return [row(x) for x in db.query(MonthClose).order_by(MonthClose.month).all()]
@router.post('/month-closes')
def do_close(b:MonthInput,db=Depends(database),u=Depends(admin)):return row(close_month(db,u,b.month))
@router.post('/month-closes/reopen')
def do_reopen(b:MonthInput,db=Depends(database),u=Depends(admin)):return row(reopen_month(db,u,b.month))
@router.get('/allocations')
def allocations(db=Depends(database),u=Depends(current),month:str|None=None,meter_id:int|None=None):
 q=db.query(MeterMonthAllocation).order_by(MeterMonthAllocation.month,MeterMonthAllocation.meter_id)
 if month:q=q.filter(MeterMonthAllocation.month==month)
 if meter_id:q=q.filter(MeterMonthAllocation.meter_id==meter_id)
 return [row(x) for x in q.all()]
@router.get('/adjustments')
def adjustments(db=Depends(database),u=Depends(current)):
 return [row(x) for x in db.query(Adjustment).order_by(Adjustment.id.desc()).limit(200).all()]
@router.get('/meter-jobs')
def jobs(db=Depends(database),u=Depends(current)):
 return [row(x) for x in db.query(MeterJob).order_by(MeterJob.id.desc()).limit(100).all()]
@router.post('/meter-jobs/pump')
def manual_pump(db=Depends(database),u=Depends(admin)):return {'processed':pump(db,u.email)}

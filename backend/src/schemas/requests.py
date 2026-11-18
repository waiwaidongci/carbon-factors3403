from pydantic import BaseModel,Field
class Login(BaseModel): email:str; password:str
class DataInput(BaseModel): data:dict
class RunInput(BaseModel): name:str=Field(min_length=2,max_length=120); policy_id:int; period:str='全部'
class ReviewInput(BaseModel): note:str=Field(min_length=4,max_length=2000)
class ScheduleInput(BaseModel): name:str=Field(min_length=2,max_length=120); run_id:int; interval_seconds:int=Field(ge=5,le=86400)
class MeterInput(BaseModel):
 code:str=Field(min_length=1,max_length=60); name:str=Field(default='',max_length=160)
 tz_name:str=Field(min_length=1,max_length=80); unit:str='kWh'; replaces_id:int|None=None
class ReadingInput(BaseModel):
 meter_id:int; read_at:str=Field(min_length=4,max_length=40); cumulative:float; kind:str='regular'; note:str=''
class FactorInput(BaseModel): month:str=Field(min_length=7,max_length=7); kg_per_unit:float; unit:str='kWh'
class MonthInput(BaseModel): month:str=Field(min_length=7,max_length=7)

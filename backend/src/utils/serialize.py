from decimal import Decimal
from datetime import datetime
def row(obj):
 out={}
 for c in obj.__table__.columns:
  v=getattr(obj,c.name)
  if isinstance(v,Decimal):v=float(v)
  elif isinstance(v,datetime):v=v.isoformat()
  out[c.name]=v
 return out

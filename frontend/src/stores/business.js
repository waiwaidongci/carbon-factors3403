import {loadAll} from '../api/business.js';
export const store={session:JSON.parse(localStorage.getItem('session')||'null'),data:{sources:[],periods:[],policies:[],runs:[],schedules:[],deliveries:[],audits:[],meters:[],readings:[],month_factors:[],month_closes:[],allocations:[],adjustments:[],meter_jobs:[]},selected:null,meterId:Number(localStorage.getItem('meterId')||0)||null};
export async function reload(){store.data=await loadAll()}
export function setSession(value){store.session=value;if(value)localStorage.setItem('session',JSON.stringify(value));else localStorage.removeItem('session')}
export function selectMeter(id){store.meterId=id;if(id)localStorage.setItem('meterId',String(id));else localStorage.removeItem('meterId')}

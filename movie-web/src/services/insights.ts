export type Metric={metric_id:string;value:any;status:string;source:string;scope:string;window:{from:string|null;to:string|null};sample_size:number;as_of:string;definition_version:string;unit:string;denominator:number|null;notes:string}
export async function loadInsights(path:string,from:string,to:string,signal:AbortSignal):Promise<Metric[]>{
 const q=new URLSearchParams();if(from)q.set('from',from);if(to)q.set('to',to)
 const response=await fetch('/api/v1/insights/'+path+'?'+q,{credentials:'same-origin',signal});const body=await response.json()
 if(!response.ok)throw new Error(response.status===401?'登录已失效，请重新登录。':response.status===400?'时间范围无效，请使用不超过366天的有效区间。':response.status===429?'刷新过于频繁，请稍后重试。':'数据暂时不可用，请重试。')
 return body.data.metrics
}

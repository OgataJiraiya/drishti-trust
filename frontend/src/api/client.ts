const BASE=(import.meta.env.VITE_DRISHTI_API_URL??'').replace(/\/$/,'');
export class ApiError extends Error{constructor(message:string,public status?:number){super(message);this.name='ApiError'}}
export async function getJson<T>(path:string,validate:(v:unknown)=>v is T):Promise<T>{
 const controller=new AbortController(); const timer=setTimeout(()=>controller.abort(),8000);
 try{const res=await fetch(`${BASE}${path}`,{headers:{Accept:'application/json'},signal:controller.signal});if(!res.ok)throw new ApiError(res.status===404?'Requested resource was not found.':res.status===422?'The backend rejected this request.':res.status>=500?'The backend could not complete this request.':`Request failed (${res.status}).`,res.status);let value:unknown;try{value=await res.json()}catch{throw new ApiError('The backend returned an unreadable response.',res.status)}if(!validate(value))throw new ApiError('The backend returned an unexpected response shape.',res.status);return value}
 catch(e){if(e instanceof ApiError)throw e;if(e instanceof DOMException&&e.name==='AbortError')throw new ApiError('The backend did not respond within 8 seconds.');throw new ApiError('The local DRISHTI backend is unreachable.') }finally{clearTimeout(timer)}
}
export const isRecord=(v:unknown):v is Record<string,unknown>=>typeof v==='object'&&v!==null;

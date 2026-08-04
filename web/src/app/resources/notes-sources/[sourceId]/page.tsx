"use client";

import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { signIn, useSession } from "next-auth/react";

type Resolution = { state:string; message?:string; highlight_text:string; context_before:string; context_after:string; source:{source_id:string;title:string}; locator:{page_file?:string;page_position?:number} };

export default function NotesSourceReader() {
  const params=useParams(); const search=useSearchParams();
  const session=useSession(); const authenticated=process.env.NODE_ENV!=="production"||session.status==="authenticated";
  const sourceId=decodeURIComponent(Array.isArray(params.sourceId)?params.sourceId[0]:String(params.sourceId));
  const citationId=search.get('citation'); const [data,setData]=useState<Resolution|null>(null); const [error,setError]=useState('');
  useEffect(()=>{if(!citationId){setError('缺少 citation ID');return;}fetch(`/api/canonical-repository/citations/${encodeURIComponent(citationId)}`).then(async response=>{const payload=await response.json();if(!response.ok)throw new Error(payload.detail??'引用不存在');if(payload.source.source_id!==sourceId)throw new Error('引用與來源不相符');setData(payload);}).catch(err=>setError(err.message));},[citationId,sourceId]);
  if(session.status==="loading"&&process.env.NODE_ENV==="production")return <main className="p-10 text-center">確認存取權限中…</main>;
  if(!authenticated)return <main className="mx-auto max-w-3xl px-6 py-16 text-center"><h1 className="text-2xl font-bold">原始筆記僅供教會成員與同工查閱</h1><button onClick={()=>signIn('google')} className="mt-5 rounded-lg bg-sky-600 px-5 py-2 font-semibold text-white">使用 Google 登錄</button></main>;
  if(error)return <main className="mx-auto max-w-4xl p-10 text-red-700">{error}</main>;
  if(!data)return <main className="p-10 text-center">讀取來源中…</main>;
  const imageUrl=data.locator.page_file?`/api/admin/notes-to-sermon/image/${data.locator.page_file.split('/').map(encodeURIComponent).join('/')}`:null;
  return <main className="mx-auto max-w-6xl px-6 py-10"><p className="text-sm font-semibold text-sky-700">原始筆記來源</p><h1 className="mt-1 text-3xl font-bold">{data.source.title}</h1><p className="mt-2 text-slate-600">第 {(data.locator.page_position??0)+1} 頁 · 引用狀態：{data.state}</p>
    {data.state!=="valid"?<div className="mt-5 rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-900">{data.message??'來源需要重新確認，因此不顯示誤導性的高亮。'}</div>:null}
    <div className="mt-6 grid gap-6 lg:grid-cols-2">{imageUrl?<div className="overflow-hidden rounded-xl border bg-slate-100 p-2">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={imageUrl} alt={data.locator.page_file??'原始筆記'} className="h-auto w-full"/>
    </div>:null}<section className="rounded-xl border bg-white p-6 shadow-sm"><h2 className="text-lg font-bold">OCR 對照與高亮</h2><p className="mt-5 whitespace-pre-wrap text-lg leading-9 text-slate-700">{data.context_before}<mark id="citation-highlight" className="rounded bg-amber-200 px-1">{data.highlight_text}</mark>{data.context_after}</p><p className="mt-6 text-xs text-slate-500">高亮文字必須與該頁 OCR 完全一致；來源改變後系統會標為 stale，不會自動猜測。</p></section></div>
  </main>;
}

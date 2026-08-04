"use client";

import { useParams } from "next/navigation";
import { useEffect,useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CitationMediaPlayer } from "@/app/components/canonical-repository/CitationMediaPlayer";

const SHOW_PUBLIC_MANUSCRIPT = false;

export default function PublicUnitPage(){const params=useParams();const id=decodeURIComponent(Array.isArray(params.unitId)?params.unitId[0]:String(params.unitId));const[data,setData]=useState<any>(null);const[error,setError]=useState('');useEffect(()=>{fetch(`/api/canonical-repository/units/${encodeURIComponent(id)}`,{cache:'no-store'}).then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d.detail??'找不到單元');setData(d)}).catch(e=>setError(e.message));},[id]);if(error)return <main className="p-10 text-center text-red-700">{error}</main>;if(!data)return <main className="p-10 text-center">讀取中…</main>;return <main className="mx-auto max-w-5xl px-6 py-12"><div className="flex flex-wrap gap-2">{data.unit.primary_bible_refs.map((r:any)=><span key={r.osis} className="rounded bg-sky-50 px-2 py-1 text-sm font-semibold text-sky-700">{r.display}</span>)}</div><h1 className="mt-4 text-4xl font-bold">{data.unit.title}</h1>{SHOW_PUBLIC_MANUSCRIPT?<div className="prose mt-8 max-w-none"><ReactMarkdown remarkPlugins={[remarkGfm]}>{data.manuscript_markdown}</ReactMarkdown></div>:null}<section className={`${SHOW_PUBLIC_MANUSCRIPT?'mt-10 border-t pt-6':'mt-8'}`}><h2 className="text-2xl font-bold">原始来源</h2><div className="mt-4 space-y-5">{data.citations.map((c:any)=><article key={c.citation_id} className="rounded-lg border p-4"><a href={c.deep_link_url} target="_blank" rel="noopener noreferrer" className="font-bold text-indigo-700 hover:underline">{c.source.title}</a><CitationMediaPlayer source={c.source} startTime={c.locator?.start_time}/><p className="mt-4 text-sm leading-7 text-slate-700">{c.highlight_text}</p></article>)}</div></section></main>}

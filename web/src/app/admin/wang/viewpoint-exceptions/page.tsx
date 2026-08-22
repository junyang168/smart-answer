import { Suspense } from "react";
import { ExceptionInbox } from "./ExceptionInbox";

export default function ViewpointExceptionsPage() {
  return <Suspense fallback={<p className="py-10 text-sm text-slate-500">载入观点例外…</p>}><ExceptionInbox /></Suspense>;
}

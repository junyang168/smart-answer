"use client";
import { Answer } from "@/app/components/answer";
import { Relates } from "@/app/components/relates";
import { Sources } from "@/app/components/sources";
import { Relate } from "@/app/interfaces/relate";
import { Source } from "@/app/interfaces/source";
import { fetchAnswer } from "@/app/utils/fetch-answer";
import { Annoyed } from "lucide-react";
import { FC, useEffect, useState } from "react";

export const Result: FC<{org_id:string, query: string; rid: string }> = ({ org_id, query, rid }) => {
  const [sources, setSources] = useState<Source[]>([]);
  const [markdown, setMarkdown] = useState<string>("");
  const [relates, setRelates] = useState<Relate[] | null>(null);
  const [error, setError] = useState<number | null>(null);

  console.log('16: ', query, rid, org_id)
  
  useEffect(() => {
    console.log('org_id ', org_id)
    const controller = new AbortController();
    void fetchAnswer(
      controller,
      query,
      org_id,
      rid,
      setSources,
      setMarkdown,
      setRelates,
      setError,
    );
    return () => {
      controller.abort();
    };
  }, [query]);
  return (
    <div className="flex flex-col gap-8">
      <Answer markdown={markdown} sources={sources}></Answer>
      <Sources sources={sources}></Sources>
      {error && (
        <div className="absolute inset-4 flex items-center justify-center bg-white/40 backdrop-blur-sm">
          <div className="p-4 bg-white shadow-2xl rounded text-blue-500 font-medium flex gap-4">
            <Annoyed></Annoyed>
            {error === 429
              ? "Sorry, you have made too many requests recently, try again later."
              : "Sorry, we might be overloaded, try again later."}
          </div>
        </div>
      )}
    </div>
  );
};

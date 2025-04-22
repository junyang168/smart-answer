"use client";
import { Answer } from "@/app/components/answer";
import { Title } from "@/app/components/title";

import { Relates } from "@/app/components/relates";
import { Sources } from "@/app/components/sources";
import { Relate } from "@/app/interfaces/relate";
import { Source } from "@/app/interfaces/source";
import { fetchAnswer } from "@/app/utils/fetch-answer";
import { chat_entry } from '@/app/interfaces/chat_entry'

import { Annoyed } from "lucide-react";
import { FC, useEffect, useState } from "react";
import { IChatItemProps } from 'react-chat-elements'

import 'react-chat-elements/dist/main.css'
import { ChatList } from 'react-chat-elements'


export const Result: FC<{org_id:string, query: string; rid: string }> = ({ org_id, query, rid }) => {
  const [sources, setSources] = useState<Source[]>([]);
  const [markdown, setMarkdown] = useState<string>("");
  const [chat_history, setChatHistory] = useState<IChatItemProps[]>([]);
  const [error, setError] = useState<number | null>(null);

  console.log('16: ', query, rid, org_id)
  let history: chat_entry[] = []
  history.push({
    role: "user",
    title: query
  });
  
  useEffect(() => {
    const controller = new AbortController();
    void fetchAnswer(
      controller,
      history,
      setSources,
      setMarkdown,
      setError,
    );
    return () => {
      controller.abort();
    };
  }, [query, org_id, rid]);
  return (
    <div>
      <Title org_id={org_id} query={query}></Title>
      <ChatList  className='chat-list' dataSource={chat_history} id='chat_list_1' lazyLoadingImage="/" />

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
    </div>
  );
};

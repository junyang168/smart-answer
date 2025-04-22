import { Relate } from "@/app/interfaces/relate";
import { Source } from "@/app/interfaces/source";
import { chat_entry } from '@/app/interfaces/chat_entry'

const LLM_SPLIT = "__LLM_RESPONSE__";
const RELATED_SPLIT = "__RELATED_QUESTIONS__";

export const fetchAnswer = async (
  controller: AbortController,
  chat_history: chat_entry[],
  onSources: (value: Source[]) => void,
  onMarkdown: (value: string) => void,
  onError?: (status: number) => void,
) => {
  let api_url = "/sc_api"
  api_url = api_url + '/qa/junyang168@gmail.com'

  let history: { role: string; content: string; }[] = []
  chat_history.forEach((entry) => {
    if (entry.role === "user" || entry.role === "assistant") {
      history.push({
        role: entry.role,
        content: entry.title,
      });
    }
  });
  const response = await fetch(api_url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "*./*",
    },
    signal: controller.signal,
    body: JSON.stringify( history),
  });
  if (response.status !== 200) {
    onError?.(response.status);
    return;
  }

  const result =  await response.json()
  onMarkdown(result.answer)

  let sources : Source[] = [] 
  for( var ref of result.quotes) {
    const link = `${ref.Link}&s=${encodeURIComponent(ref.Title)}&d=${encodeURIComponent(ref.Index)}`
    const src : Source = { id: ref.Link, name: ref.Title, displayUrl : ref.Link, url: link,
        snippet:ref.Title,
        deepLinks: [],
        dateLastCrawled: '',
        cachedPageUrl: '',
        language: 'en',
        isNavigational: true,
        isFamilyFriendly: false,
        primaryImageOfPage: {
            thumbnailUrl: '',
            width: 10,
            height: 10,
            imageId: ''
          }        
    }
    sources.push(src)
  }
  onSources(sources)

};

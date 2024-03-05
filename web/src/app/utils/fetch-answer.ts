import { Relate } from "@/app/interfaces/relate";
import { Source } from "@/app/interfaces/source";
import { IChatItemProps } from 'react-chat-elements'

const LLM_SPLIT = "__LLM_RESPONSE__";
const RELATED_SPLIT = "__RELATED_QUESTIONS__";

export const fetchAnswer = async (
  controller: AbortController,
  query: string,
  org: string,
  search_uuid: string,
  onNewQuestion: (value: string) => void,
  onChatHistory: (value: IChatItemProps[]) => void,
  onSources: (value: Source[]) => void,
  onMarkdown: (value: string) => void,
  onRelates: (value: Relate[]) => void,
  onError?: (status: number) => void,
) => {
  const decoder = new TextDecoder();
  let uint8Array = new Uint8Array();
  let chunks = "";
  let sourcesEmitted = false;

  const env = process.env.NODE_ENV;
  let api_url = ""
  if( env != 'production') {
    api_url = 'http://localhost:50000'
  }
  api_url = api_url + '/get_answer'

  const response = await fetch(api_url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "*./*",
    },
    signal: controller.signal,
    body: JSON.stringify({
      org_id:org,
      question:query,
      sid:search_uuid
    }),
  });
  if (response.status !== 200) {
    onError?.(response.status);
    return;
  }

  const result =  await response.json()
  onMarkdown(result.answer)
  onNewQuestion(result.new_question)

  let sources : Source[] = [] 
  for( var ref of result.references) {
    const src : Source = { id: ref.Link, name: ref.Title, displayUrl : ref.Link, url: ref.Link,
        snippet:"",
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

  let history : IChatItemProps[] = [] 
  result.chat_history.push({Role:'human', Message:query})
  for( var chat_item of result.chat_history) {
    const chat_entry : IChatItemProps = { 
      id : '1',
      avatar: chat_item.Role == 'ai' ? '/lightbulb.png': '/person.png',
//      title: 'Kursat',
      subtitle: chat_item.Message,
//      date: new Date(),
//      unread: 3,           
    }
    history.push(chat_entry)
  }
  onChatHistory(history)


};

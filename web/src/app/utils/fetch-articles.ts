import { Article } from "@/app/interfaces/article";
import { SunMedium } from "lucide-react";

export const fetchArticle = async (
  controller: AbortController,
  query: string,
  org: string,
  onArticles: (value: Article[]) => void,
  onError?: (status: number) => void,
) => {

  const env = process.env.NODE_ENV;
  let api_url = ""
  api_url = api_url + '/sc_api/sermons/junyang168@gmail.com'

  const response = await fetch(api_url);
  if (!response.ok) {
    onError?.(response.status);
    return;
  }
  const surmons =  await response.json()
  let articles: Article[] = [] 

  for( var surmon of surmons) {
    if(surmon.status !== "published") 
      continue;
    const article : Article = {
        id: surmon.item,
        title: surmon.title,
        theme: surmon.theme,
        snippet: surmon.summary,
        status: surmon.status,
        deliver_date: surmon.deliver_date,
        publishedUrl: "/public/" + surmon.item,
        assigned_to_name: surmon.assigned_to_name,
        thumbnail: {
          url: surmon.thumbnail,
          width: 160,
          height: 100,
          imageId: "",
        },

    }
    articles.push(article)
  }  
  onArticles(articles)



};

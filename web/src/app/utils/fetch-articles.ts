import { Sermon } from "@/app/interfaces/article";
import { SunMedium } from "lucide-react";

export const fetchSermons = async () => {

  const env = process.env.NODE_ENV;
  let api_url = ""
  api_url = api_url + '/sc_api/sermons/junyang168@gmail.com'

  const response = await fetch(api_url,{ next: { revalidate: 60 } });
  if (!response.ok) {
    console.error("Failed to fetch sermons:", response.status, response.statusText);
    throw new Error(`Failed to fetch sermons: ${response.status} ${response.statusText}`);
  }
  const sermons =  await response.json()
  let articles: Sermon[] = [] 

  for( var surmon of sermons) {
//    if(surmon.status !== "published") 
//      continue;
    const article : Sermon = {
        id: surmon.item,
        title: surmon.title,
        summary: surmon.summary,
        status: surmon.status,
        date: surmon.deliver_date,
        assigned_to_name: surmon.assigned_to_name,
        speaker: '王守仁',
        scripture: "",
        book: "",
        topic: "",
        videoUrl: "",
        audioUrl: "",
        source: "",
    };
    articles.push(article)
  }  

  return articles;

};

import { Sermon } from "@/app/interfaces/article";
import { BibleVerse } from "@/app/interfaces/article";

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
        summary: surmon.summary || '',
        status: surmon.status,
        date: surmon.deliver_date,
        published_date: surmon.published_date,
        assigned_to_name: surmon.assigned_to_name,
        assigned_to_date: surmon.assigned_to_date,
        completed_date: surmon.completed_date || surmon.published_date,
        last_updated: surmon.last_updated,
        author_name: surmon.author_name,
        type: surmon.type,
        speaker: '王守仁',
        scripture: [],
        book:  [...new Set((surmon.core_bible_verse as BibleVerse[]).map((verse: BibleVerse) => verse.book))],
        topic: [],
        videoUrl: surmon.type == null || surmon.type != "audio" ? `/web/video/${surmon.id}.mp4` : null,
        audioUrl: surmon.type === "audio" ? `/web/video/${surmon.id}.mp3` : "",
        source: surmon.source,
        theme: surmon.theme || '',
    };
    articles.push(article)
  }  

  return articles;

};

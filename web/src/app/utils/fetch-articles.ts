import { Sermon } from "@/app/interfaces/article";
import { BibleVerse } from "@/app/interfaces/article";

export const fetchSermons = async () => {

  const api_url = '/api/sc_api/sermons/junyang168@gmail.com';

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
        scripture: surmon.scripture || [],
        book: surmon.book?.length
          ? surmon.book
          : [...new Set(((surmon.core_bible_verse || []) as BibleVerse[]).map((verse: BibleVerse) => verse.book))],
        topic: surmon.topic || [],
        videoUrl: surmon.type == null || surmon.type != "audio" ? `/web/video/${surmon.id}.mp4` : null,
        audioUrl: surmon.type === "audio" ? `/web/video/${surmon.id}.mp3` : "",
        source: surmon.source,
        theme: surmon.theme || '',
        series_id: surmon.series_id,
        series_title: surmon.series_title,
        series_order: surmon.series_order,
        organization_mode: surmon.organization_mode,
        organization_mode_label: surmon.organization_mode_label,
        classification_confidence: surmon.classification_confidence,
        classification_reason: surmon.classification_reason,
        catalog_year: surmon.catalog_year,
        catalog_primary_passage: surmon.catalog_primary_passage,
        substantial_passages: surmon.substantial_passages || [],
        supporting_passages: surmon.supporting_passages || [],
        catalog_assignment: surmon.catalog_assignment,
        catalog_assignment_note: surmon.catalog_assignment_note,
        scripture_catalog_eligible: surmon.scripture_catalog_eligible,
        scripture_catalog_reason: surmon.scripture_catalog_reason,
    };
    articles.push(article)
  }  

  return articles;

};

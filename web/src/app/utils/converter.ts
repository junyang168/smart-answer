import { Sermon } from "../interfaces/article";
import { SermonSeries } from "../interfaces/article";

export const apiToUiSermon = (apiSermon: any): Sermon => {
    const uiSermon: Sermon = {
        id: apiSermon.item,
        item: apiSermon.item, // 保留原始的 item 屬性
        title: apiSermon.title,
        summary: apiSermon.summary || '',
        date: apiSermon.deliver_date,
        published_date: apiSermon.published_date ? apiSermon.published_date.split(' ')[0] : '',
        speaker: apiSermon.author_name || '',
        scripture: [], // 將所有經文合併為一個字符串
        book: apiSermon.book || '',
        topic: apiSermon.topic || '',
        videoUrl:   '',
        audioUrl:  '',
        source: '',
        keypoints: apiSermon.keypoints || '',
        theme: apiSermon.theme || '',
        core_bible_verses: {},
        status: apiSermon.status || '',
        assigned_to_name: apiSermon.assigned_to_name || '',
        markdownContent: apiSermon.markdownContent || '',
    }
    return uiSermon

};

export const apiToUiSermonSeries = (apiSeries: any): SermonSeries => {
    const uiSeries: SermonSeries = {
        id: apiSeries.id,
        title: apiSeries.title,
        summary: apiSeries.summary || '',
        topics: apiSeries.topics || [],
        keypoints: apiSeries.keypoints || '',
        sermons: apiSeries.articles.map((article: any) => apiToUiSermon(article)),
    };
    return uiSeries;
};
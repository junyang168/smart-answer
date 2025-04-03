import { ArticleDetail, Paragraph } from "@/app/interfaces/article_detail";

export const fetchArticleDetail = async (
  item: string,
  onError?: (status: number) => void,
) : Promise<ArticleDetail>  => {

    let api_url = process.env.ARTICLE_SERVICE_URL + item
    const response = await fetch(api_url);
    if (!response.ok) {
    onError?.(response.status);
    return {} as ArticleDetail;
    }
    const data =  await response.json()
    const article : ArticleDetail = {
        id: item,
        title: data.metadata.title,
        theme: data.metadata.theme,
        snippet: data.metadata.summary,
        paragraphs: []
    }
    for (let i = 0; i < data.script.length; i++) {
        const paragraph: Paragraph = {
            index: data.script[i].index,
            text: data.script[i].text,
            type: data.script[i].type,
            user_id: data.script[i].user_id
        }
        article.paragraphs.push(paragraph)
    }
    return article
};

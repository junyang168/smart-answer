import { SearchBox } from "@/app/components/searchbox";
import React from "react";
import { fetchArticleDetail } from "@/app/utils/fetch-article-detail";
import { ArticleDetail } from "../interfaces/article_detail";


// Define the expected query parameters
interface PageProps {
    searchParams: {
      i?: string; 
      o?: string; 
      rid?: string;
    };
  }

export default async function ArticlePage( {searchParams} : PageProps) {
    const org_id = searchParams.o || "";
    const rid = searchParams.rid || "";
    const item :string = searchParams.i || "";

    const article : ArticleDetail =  await fetchArticleDetail(item, (status) => {
        console.log("Error fetching article detail:", status);
    });


  return (
    <div className="flex h-screen bg-gray-50">

    {/* Main Content */}
    <div className="flex-1 flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="bg-white p-2 flex items-center justify-between border-b border-gray-200">
        <SearchBox org_id={org_id} rid={rid} />
      </div>
      <div className="flex-1 overflow-y-auto p-4 bg-gray-50">
              <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-6">
              <div className="col-span-3">
        {/* Article Header */}
        <div className="bg-white p-4 flex items-center justify-between border-b border-gray-200">
            <h1 className="text-2xl font-bold">{article?.title || "Untitled Article"}</h1>
            <div className="flex items-center">
                <span className="text-gray-500 text-sm">{article?.snippet || "No snippet available."}</span>
            </div>
        </div>
        {/* Article Content */}            
        <div className="p-4"></div>
            {article.paragraphs?.map((paragraph, index) => (
                <p key={index} id={paragraph.index} className="mb-2">
                    {paragraph.text}
                </p>
            )) || <p>No content available.</p>}
        </div>


            </div>
        </div>

        </div>

    </div>


  )  
}

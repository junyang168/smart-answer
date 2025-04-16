import { SearchBox } from "@/app/components/searchbox";
import React from "react";
import { fetchArticleDetail } from "@/app/utils/fetch-article-detail";
import { ArticleDetail } from "../interfaces/article_detail";
import { CopilotChat  } from "@/app/components/copilot";


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
      <div className="bg-white p-2 border-b border-gray-200">
  <div className="flex flex-col">
    <h1 className="text-3xl font-bold text-center">{article?.title}</h1>
    <nav className="flex text-sm text-gray-500 my-2" aria-label="Breadcrumb">
      <ol className="inline-flex items-center space-x-1 md:space-x-3">
        <li className="inline-flex items-center">
          <a href="/" className="text-gray-700 hover:text-gray-900 inline-flex items-center">
            Home
          </a>
        </li>
        <li>
          <div className="flex items-center">
            <span className="mx-2">/</span>
            <span className="text-gray-500">{article?.title || "Untitled Article"}</span>
          </div>
        </li>
      </ol>
    </nav>
  </div>
</div>
      <div className="flex-1 overflow-y-auto p-4 bg-gray-50">
              <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-6">
              <div className="col-span-3">
        {/* Article Header */}
        <div className="bg-white p-4 flex items-center justify-between border-b border-gray-200">
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

        <CopilotChat item_id={item} /> 

    </div>


  )  
}

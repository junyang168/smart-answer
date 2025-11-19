import React from "react";
import { fetchArticleDetail } from "@/app/utils/fetch-article-detail";
import { ArticleDetail } from "../interfaces/article_detail";



// Define the expected query parameters
interface PageProps {
  searchParams: {
    i?: string;
    o?: string;
    rid?: string;
    s?: string;
    d?: string;
  };
}

export default async function ArticlePage({ searchParams }: PageProps) {
  const org_id = searchParams.o || "";
  const rid = searchParams.rid || "";
  const item: string = searchParams.i || "";
  const quote: string = searchParams.s || "";
  const index: string = searchParams.d || "";

  const article: ArticleDetail = await fetchArticleDetail(item, quote, (status) => {
    console.log("Error fetching article detail:", status);
  });

  const quote_id = null



  return (
    <div className="flex h-screen bg-gray-50">

      {/* Main Content */}
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        {/* Header */}
        <div className="bg-white p-2 border-b border-gray-200">
          <div className="flex flex-col">
            <h1 className="text-3xl font-bold text-center">{article?.title}</h1>
            <nav className="flex text-sm text-gray-500 my-0" aria-label="Breadcrumb">
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
                <p
                  key={index}
                  id={paragraph.index}
                  className={`mb-2}`}
                >
                  {paragraph.text}
                </p>
              )) || <p>No content available.</p>}
            </div>

          </div>
        </div>

      </div>



      {/* Scroll to specific paragraph if quote is provided */}
      {index && (
        <script
          dangerouslySetInnerHTML={{
            __html: `
          document.addEventListener('DOMContentLoaded', function() {
          var index = '${index}';
          const idx = index.split('-')
          const idx_start = idx[0]
          const idx_end = idx.length > 1 ? idx[1] : idx[0]
          const eStart = document.getElementById(idx_start);
          const eEnd = document.getElementById(idx_end);
          
          if (eStart && eEnd) {
            current = eStart;
            while (current && current != eEnd) {
              current.classList.add('bg-yellow-200');
              current = current.nextElementSibling;
            }
            eEnd.classList.add('bg-yellow-200');
            eStart.scrollIntoView({ behavior: 'smooth', block: 'center' });

          }

      });
          `,
          }}
        />
      )}

    </div>


  )
}

"use client";
import React, { FC, useEffect, useState } from "react";
import { Search, Menu, Home, Clock, ThumbsUp, Download, History, PlaySquare, Film, Music, User, Bell, Edit } from 'lucide-react';
import { fetchArticle } from "@/app/utils/fetch-articles";
import { Article } from "../interfaces/article";
import { getSearchUrl } from "@/app/utils/get-search-url";
import { useRouter } from "next/navigation";

export const Playlist: FC<{org_id:string, rid:string }> = ({ org_id, rid}) => {
  const [articles, setArticles] = useState<Article[]>([]);
  const [error, setError] = useState<number | null>(null);
  const query = "";
  useEffect(() => {
      const controller = new AbortController();
      void fetchArticle(
        controller,
        query,      
        org_id,
        setArticles,
        setError,
      );
      return () => {
        controller.abort();
      };
    }, [query, org_id]);
    const [selectedVideo, setSelectedVideo] = useState(0);

    const [value, setValue] = useState("");
    const router = useRouter();
        
      return (
        <div className="flex h-screen bg-gray-50">
    
          {/* Sidebar */}
          <div className="w-60 bg-white border-r border-gray-200 flex flex-col h-full overflow-y-auto flex-shrink-0">
            <div className="p-4 flex items-center gap-2">
              <Menu className="w-6 h-6" />
              <div className="flex items-center">
                <span className="font-bold ml-1"></span>
              </div>
            </div>
    

          </div>
    
          {/* Main Content */}
          <div className="flex-1 flex flex-col h-full overflow-hidden">
            {/* Header */}
            <div className="bg-white p-2 flex items-center justify-between border-b border-gray-200">
              <div className="flex-1 max-w-3xl mx-auto relative">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (value) {
                    setValue("");
                    const url = getSearchUrl(org_id, encodeURIComponent(value), rid)                    
                    router.push(url);
                  }
                }}
              >
                <input
                  type="text"
                  placeholder="Search or Ask a question..."
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  autoFocus        
                  className="w-full py-2 px-4 pr-12 bg-gray-100 rounded-full outline-none border border-gray-300"
                />
                <div className="absolute right-0 top-0 h-full flex items-center pr-3">
                  <Search className="w-5 h-5 text-gray-500" />
                </div>
                </form>
                </div>


              <div className="flex items-center gap-4 ml-4">
                <button 
                  className="flex items-center gap-1 py-1 px-4 border border-gray-300 rounded-full"
                  onClick={() => {
                  window.location.href = "/web/index.html";
                  }}
                >
                  <Edit className="w-5 h-5" />
                  <span>編輯</span>
                </button>
              </div>
            </div>
    
            {/* Playlist Content */}
            <div className="flex-1 overflow-y-auto p-4 bg-gray-50">
              <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-6">
    
                {/* Right Video List */}
                <div className="col-span-3">
                  <div className="bg-white rounded-lg shadow-sm">
                    {articles.map((article, index) => (
                        <div 
                        key={article.id} 
                        className={`flex p-2 border-b border-gray-100 ${index === selectedVideo ? 'bg-gray-100' : 'hover:bg-gray-50'} cursor-pointer`}
                        onClick={() => {
                          setSelectedVideo(index);
                          window.location.href = article.publishedUrl;
                        }}
                        >
                        <div className="mr-4 text-lg font-medium w-6 text-center"></div>
                        <div className="relative w-40 h-24 flex-shrink-0">
                          <img src={article.thumbnail.url} alt={article.title} className="w-full h-full object-cover rounded-md" />
                          <div className="absolute bottom-1 right-1 bg-black bg-opacity-70 text-white text-xs px-1 rounded">
                          
                          </div>
                        </div>
                        <div className="ml-3 flex-1">
                          <h3 className="font-medium">{article.theme}</h3>
                          <p className="text-gray-600 text-sm">{article.snippet}</p>
                          <div className="flex text-sm text-gray-500">
                          <span>認領人</span>
                          <span className="mx-1">{article.assigned_to_name}</span>
                          <span>{article.deliver_date}</span>
                          </div>
                        </div>
                        <button className="p-2 text-gray-500 self-start ml-2">
                          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <circle cx="12" cy="12" r="1"></circle>
                          <circle cx="19" cy="12" r="1"></circle>
                          <circle cx="5" cy="12" r="1"></circle>
                          </svg>
                        </button>
                        </div>
                    ))}
                  </div>

                </div>
              </div>
            </div>
          </div>
        </div>
      );

}

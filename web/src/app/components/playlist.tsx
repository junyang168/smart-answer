"use client";
import React, { FC, useEffect, useState } from "react";
import { Search, Menu, Home, Clock, ThumbsUp, Download, History, PlaySquare, Film, Music, User, Bell, Plus } from 'lucide-react';
import { fetchArticle } from "@/app/utils/fetch-articles";
import { Article } from "../interfaces/article";

export const Playlist: FC<{org_id:string, query: string}> = ({ org_id, query}) => {
  const [articles, setArticles] = useState<Article[]>([]);
  const [error, setError] = useState<number | null>(null);
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
    
      const sidebarItems = [
        { icon: <Home className="w-6 h-6" />, label: 'Home' },
        { icon: <Film className="w-6 h-6" />, label: 'Shorts' },
        { icon: <PlaySquare className="w-6 h-6" />, label: 'Subscriptions' },
        { icon: <Music className="w-6 h-6" />, label: 'YouTube Music' }
      ];
    
      const youItems = [
        { icon: <User className="w-6 h-6" />, label: 'You' },
        { icon: <History className="w-6 h-6" />, label: 'History' },
        { icon: <PlaySquare className="w-6 h-6" />, label: 'Playlists' },
        { icon: <PlaySquare className="w-6 h-6" />, label: 'Your videos' },
        { icon: <Clock className="w-6 h-6" />, label: 'Watch later' },
        { icon: <ThumbsUp className="w-6 h-6" />, label: 'Liked videos' },
        { icon: <Download className="w-6 h-6" />, label: 'Downloads' }
      ];
    
      const subscriptionChannels = [
        { name: 'DW News', icon: '/api/placeholder/24/24' },
        { name: '實宇新聞 頻道', icon: '/api/placeholder/24/24' },
        { name: 'Al Jazeera English', icon: '/api/placeholder/24/24' },
        { name: 'ABC News', icon: '/api/placeholder/24/24' },
        { name: 'ShanghaiEye魔都眼', icon: '/api/placeholder/24/24' }
      ];
    
      return (
        <div className="flex h-screen bg-gray-50">
    
          {/* Sidebar */}
          <div className="w-60 bg-white border-r border-gray-200 flex flex-col h-full overflow-y-auto flex-shrink-0">
            <div className="p-4 flex items-center gap-2">
              <Menu className="w-6 h-6" />
              <div className="flex items-center">
                <div className="bg-red-600 text-white p-1 rounded-lg flex items-center">
                  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="white">
                    <path d="M19.615 3.184c-3.604-.246-11.631-.245-15.23 0-3.897.266-4.356 2.62-4.385 8.816.029 6.185.484 8.549 4.385 8.816 3.6.245 11.626.246 15.23 0 3.897-.266 4.356-2.62 4.385-8.816-.029-6.185-.484-8.549-4.385-8.816zm-10.615 12.816v-8l8 3.993-8 4.007z"></path>
                  </svg>
                </div>
                <span className="font-bold ml-1">Premium</span>
              </div>
            </div>
    
            <div className="overflow-y-auto flex-1">
              <div className="mb-4">
                {sidebarItems.map((item, index) => (
                  <div key={index} className="flex items-center p-3 hover:bg-gray-100 cursor-pointer">
                    {item.icon}
                    <span className="ml-6">{item.label}</span>
                  </div>
                ))}
              </div>
    
              <div className="border-t border-gray-200 pt-2 mb-4">
                <div className="flex items-center p-3 hover:bg-gray-100 cursor-pointer">
                  <User className="w-6 h-6" />
                  <span className="ml-6 font-medium">You</span>
                  <span className="ml-1">›</span>
                </div>
                {youItems.map((item, index) => (
                  <div key={index} className="flex items-center p-3 hover:bg-gray-100 cursor-pointer">
                    {item.icon}
                    <span className="ml-6">{item.label}</span>
                  </div>
                ))}
              </div>
    
              <div className="border-t border-gray-200 pt-2 mb-4">
                <div className="p-3 font-medium">Subscriptions</div>
                {subscriptionChannels.map((channel, index) => (
                  <div key={index} className="flex items-center p-3 hover:bg-gray-100 cursor-pointer">
                    <div className="w-6 h-6 rounded-full bg-gray-300 overflow-hidden">
                      <img src={channel.icon} alt={channel.name} className="w-full h-full object-cover" />
                    </div>
                    <span className="ml-6">{channel.name}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
    
          {/* Main Content */}
          <div className="flex-1 flex flex-col h-full overflow-hidden">
            {/* Header */}
            <div className="bg-white p-2 flex items-center justify-between border-b border-gray-200">
              <div className="flex-1 max-w-3xl mx-auto relative">
                <input
                  type="text"
                  placeholder="Search"
                  className="w-full py-2 px-4 pr-12 bg-gray-100 rounded-full outline-none border border-gray-300"
                />
                <div className="absolute right-0 top-0 h-full flex items-center pr-3">
                  <Search className="w-5 h-5 text-gray-500" />
                </div>
              </div>
              <div className="flex items-center gap-4 ml-4">
                <button className="flex items-center gap-1 py-1 px-4 border border-gray-300 rounded-full">
                  <Plus className="w-5 h-5" />
                  <span>Create</span>
                </button>
                <Bell className="w-6 h-6" />
                <div className="w-8 h-8 rounded-full bg-orange-500 flex items-center justify-center text-white">
                  <span>U</span>
                </div>
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
                        <div className="mr-4 text-lg font-medium w-6 text-center">{article.id}</div>
                        <div className="relative w-40 h-24 flex-shrink-0">
                          <img src={article.publishedUrl} alt={article.title} className="w-full h-full object-cover rounded-md" />
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

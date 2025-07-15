import { Footer } from "@/app/components/footer";
import { Logo } from "@/app/components/logo";
import { PresetQuery } from "@/app/components/preset-query";
import { Search } from "@/app/components/search";
import React from "react";
import { useSearchParams } from "next/navigation";
import { Suspense } from 'react'
import { nanoid } from "nanoid";
import { getServerSession } from "next-auth";
import { authConfig} from "@/app/utils/auth";
import { Playlist } from "@/app/components/playlist";
import RootLayout  from './layout';
import type { NextPage } from 'next';



const HomePage: NextPage = () => {
  return (
      <div className="container mx-auto px-6 py-12">
        <div className="bg-white p-8 rounded-lg shadow-lg text-center">
          <h2 className="font-display text-4xl font-bold text-gray-800 mb-4">
            歡迎來到達拉斯聖道教會
          </h2>
          <p className="text-lg text-gray-600 max-w-2xl mx-auto">
            這裡是每個頁面的獨特內容。這個主體佈局將包裹著我們所有的頁面，提供一致的導航和頁腳。
          </p>
          <div className="mt-8">
            <a 
              href="/resources" 
              className="bg-[#8B4513] text-white font-bold py-3 px-8 rounded-full hover:bg-opacity-90 transition-all text-lg"
            >
              探索資源中心
            </a>
          </div>
        </div>
      </div>
    
  );
};

export default HomePage;
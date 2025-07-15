// app/resources/sermons/page.tsx
import  SermonFilterControls  from '@/app/components/sermons/SermonFilterControls';
import { SermonListItem } from '@/app/components/sermons/SermonListItem';
import { PaginationControls } from '@/app/components/sermons/PaginationControls';
import { Sermon } from '@/app/interfaces/article';
import {SermonSearchBar} from '@/app/components/sermons/SermonSearchBar';
import {SermonSidebar} from '@/app/components/sermons/SermonSidebar';

// --- 模擬數據和後端邏輯 ---
// 在真實應用中，您會調用一個 API 來獲取數據。
// 這裡我們用一個大的數據集來模擬。
const allSermons: Sermon[] = [
  { id: '1', title: '福音基礎 (一): 什麼是福音?', speaker: '王守仁 牧師', date: '2025年01月05日', scripture: '羅馬書 1:16-17', book: '羅馬書', topic: '福音基礎', status: '已發佈', assigned_to_name: '張三', videoUrl: '#', audioUrl: '#', summary: '摘要...',source:'' },
  { id: '2', title: '家庭系列 (一): 基督化的家庭', speaker: '李長老', date: '2025年01月12日', scripture: '以弗所書 5:22-33', book: '以弗所書', topic: '家庭系列', status: '已發佈', assigned_to_name: '李四', videoUrl: '#', audioUrl: '#', summary: '摘要...',source:''  },
  { id: '3', title: '福音基礎 (二): 因信稱義', speaker: '王守仁 牧師', date: '2025年01月19日', scripture: '羅馬書 3:21-26', book: '羅馬書', topic: '福音基礎', status: '編輯中', assigned_to_name: '張三', videoUrl: '#', audioUrl: '#', summary: '摘要...',source:''  },
  { id: '4', title: '舊約中的基督: 逾越節的羔羊', speaker: '客座講員', date: '2024年12月15日', scripture: '出埃及記 12:1-13', book: '出埃及記', topic: '舊約中的基督', status: '已發佈', assigned_to_name: '李四', videoUrl: '#', audioUrl: '#', summary: '摘要...',source:''  },
  { id: '5', title: '家庭系列 (二): 教養孩童', speaker: '王守仁 牧師', date: '2024年12月22日', scripture: '箴言 22:6', book: '箴言', topic: '家庭系列', status: '草稿', assigned_to_name: '王五', videoUrl: '#', audioUrl: '#', summary: '摘要...',source:''  },
  // ...可以按此格式添加更多數據...
];

// 模擬 API 調用和服務器端篩選
async function fetchSermons(searchParams: { [key: string]: string | string[] | undefined }) {
    const page = Number(searchParams.page ?? '1');
    const limit = 10;
    const speaker = searchParams.speaker as string;
    const book = searchParams.book as string;
    const topic = searchParams.topic as string;
    const year = searchParams.year as string;
    const status = searchParams.status as Sermon['status'];
    const assignee = searchParams.assignee as string;

    let filteredSermons = allSermons;
    if (speaker) { filteredSermons = filteredSermons.filter(s => s.speaker === speaker); }
    if (book) { filteredSermons = filteredSermons.filter(s => s.book === book); }
    if (topic) { filteredSermons = filteredSermons.filter(s => s.topic === topic); }
    if (year) { filteredSermons = filteredSermons.filter(s => s.date.startsWith(year)); }
    if (status) { filteredSermons = filteredSermons.filter(s => s.status === status); }
    if (assignee) { filteredSermons = filteredSermons.filter(s => s.assigned_to_name === assignee); }

    const startIndex = (page - 1) * limit;
    const endIndex = page * limit;
    const paginatedSermons = filteredSermons.slice(startIndex, endIndex);

    return {
        sermons: paginatedSermons,
        totalCount: filteredSermons.length,
        hasNextPage: endIndex < filteredSermons.length,
        hasPrevPage: startIndex > 0,
    }
}

export default async function SermonsPage({ searchParams }: { searchParams: { [key: string]: string | string[] | undefined } }) {

  const { sermons, totalCount, hasNextPage, hasPrevPage } = await fetchSermons(searchParams);

  return (
    <div className="container mx-auto px-6 py-12">
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold font-display text-gray-800">講道中心</h1>
        <p className="mt-4 text-lg text-gray-600 max-w-2xl mx-auto">
          使用左側的篩選器來精準定位您想重溫的寶貴信息。
        </p>
      </div>

      <div className="flex flex-col lg:flex-row">
        {/* 左側邊欄 */}
        <SermonSidebar />

        {/* 右側主內容區 */}
        <main className="flex-1">
          {/* ✅ 將搜索欄放置在這裡 */}
          <SermonSearchBar />

          <div className="mb-4 text-sm text-gray-600">
            共找到 <span className="font-bold">{totalCount}</span> 篇講道
          </div>
          {sermons.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
                {sermons.map(sermon => (
                    <SermonListItem key={sermon.id} sermon={sermon} />
                ))}
            </div>
          ) : (
            <div className="text-center py-16 bg-white rounded-lg shadow-sm">
                <h3 className="text-xl font-semibold">沒有找到符合條件的講道</h3>
                <p className="text-gray-500 mt-2">請嘗試調整或清除您的篩選條件。</p>
            </div>
          )}

          {sermons.length > 0 && (
            <PaginationControls hasNextPage={hasNextPage} hasPrevPage={hasPrevPage} />
          )}
        </main>
      </div>
    </div>
  );
}

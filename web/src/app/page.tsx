// app/page.tsx
import { HomePageView } from '@/app/components/home/HomePageView';
import { Suspense } from 'react';

// 主頁現在只是一個簡單的容器
export default function HomePage() {
  return (
    // 這裡返回的 JSX 就是將要被插入到 layout.tsx 中 {children} 位置的內容
    <Suspense fallback={<div></div>}>
        <HomePageView /> 
    </Suspense>
  );
}
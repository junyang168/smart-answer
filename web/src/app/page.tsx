// app/page.tsx
import { HomePageView } from '@/app/components/home/HomePageView';
import { Suspense } from 'react';
import { readMissionStatement } from '@/app/utils/siteContent';

export const revalidate = 0;

// 主頁現在只是一個簡單的容器
export default async function HomePage() {
  const missionStatement = await readMissionStatement();
  return (
    // 這裡返回的 JSX 就是將要被插入到 layout.tsx 中 {children} 位置的內容
    <Suspense fallback={<div></div>}>
        <HomePageView missionStatement={missionStatement.zhHant} />
    </Suspense>
  );
}
import type { Metadata } from 'next';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import ReactMarkdown from 'react-markdown';
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import styles from './our-faith.module.css';

export const metadata: Metadata = {
  title: '信仰宣告 | 達拉斯聖道教會',
  description: '達拉斯聖道教會的信仰宣告：聖經、神、人、救贖、教會、洗禮和聖餐、宣教、家庭與末世。',
};

export default async function OurFaithPage() {
  const markdown = await readFile(
    path.join(process.cwd(), 'content', 'our-faith.md'),
    'utf8',
  );

  return (
    <div className={styles.page} lang="zh-Hant">
      <Breadcrumb links={[
        { name: '首頁', href: '/' },
        { name: '關於我們', href: '/about' },
        { name: '信仰宣告' },
      ]} />
      <div className={styles.content}>
        <p className={styles.churchName}>達拉斯聖道教會</p>
        <article className={styles.manuscript}>
          <ReactMarkdown>{markdown}</ReactMarkdown>
        </article>
      </div>
    </div>
  );
}

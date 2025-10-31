import type { Metadata } from 'next';
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { DEPTH_OF_FAITH_REVALIDATE, fetchDepthOfFaithEpisodes } from './episodes';

export const metadata: Metadata = {
  title: '信仰的深度 | AI 輔助查經',
  description:
    '透過「信仰的深度」網路廣播，探索真理、堅固信心，並隨時隨地聆聽王守仁教授的聖經教導。',
};

export const revalidate = DEPTH_OF_FAITH_REVALIDATE;

export default async function DepthOfFaithPage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: 'AI 輔助查經', href: '/resources' },
    { name: '信仰的深度' },
  ];
  const episodes = await fetchDepthOfFaithEpisodes();

  return (
    <div className="bg-gray-50 pb-16">
      <Breadcrumb links={breadcrumbLinks} />

      <section className="bg-slate-900 text-white py-16 md:py-20">
        <div className="container mx-auto px-6 text-center md:text-left md:flex md:items-center md:justify-between gap-8">
          <div className="max-w-5xl mx-auto md:mx-0">
            <p className="uppercase tracking-widest text-amber-300 text-sm font-semibold mb-4">
              信仰的深度
            </p>
            <h1 className="text-4xl md:text-5xl font-bold leading-tight mb-6">
              深入神的話語，聆聽生命的更新
            </h1>
            <p className="text-lg md:text-xl text-slate-200 leading-relaxed">
              我們將王守仁教授在講道中和弟兄姊妹們在查經中的對話藉著 AI加工成簡短的網路廣播。幫助您在忙碌的生活中仍然能夠停下腳步，反思與主同行的腳步。
            </p>
          </div>
          <div className="mt-8 md:mt-0 md:text-right">
            <p className="text-slate-200 text-sm">
            </p>
            <p className="text-slate-400 text-xs mt-2">
            </p>
          </div>
        </div>
      </section>

      <section className="container mx-auto px-6 mt-12">

        {episodes.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-gray-300 bg-white px-6 py-12 text-center text-gray-500">
            節目建置中，敬請期待新的網播內容。
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            {episodes.map((episode) => (
              <article key={episode.id} className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden flex flex-col">
                <div className="p-6">
                  <span className="inline-flex items-center justify-center rounded-full bg-amber-100 text-amber-700 text-xs font-semibold px-3 py-1">
                    
                  </span>
                <h3 className="mt-4 text-2xl font-semibold text-slate-900 leading-tight">
                  {episode.title}
                </h3>
                <p className="mt-3 text-slate-600 leading-relaxed">{episode.description}</p>

                <dl className="mt-4 text-sm text-slate-500 space-y-2">
                  {episode.scripture && (
                    <div className="flex items-baseline gap-2">
                      <dt className="font-medium text-slate-700">經文焦點</dt>
                      <dd>{episode.scripture}</dd>
                    </div>
                  )}
                  {episode.publishedAt && (
                    <div className="flex items-baseline gap-2">
                      <dt className="font-medium text-slate-700">發布日期</dt>
                      <dd>
                        {new Date(episode.publishedAt).toLocaleDateString('zh-Hant', {
                          year: 'numeric',
                          month: 'long',
                          day: 'numeric',
                        })}
                      </dd>
                    </div>
                  )}
                  {episode.duration && (
                    <div className="flex items-baseline gap-2">
                      <dt className="font-medium text-slate-700">長度</dt>
                      <dd>{episode.duration}</dd>
                    </div>
                  )}
                </dl>
              </div>

              <div className="bg-slate-50 border-t border-slate-200 px-6 py-5 mt-auto">
                {episode.audioUrl ? (
                  <>
                    <audio controls className="w-full" preload="none">
                      <source src={episode.audioUrl} type="audio/mpeg" />
                      您的瀏覽器不支援 audio 元素。
                    </audio>
                    <div className="mt-3 text-right">
                      <a
                        href={episode.audioUrl}
                        className="text-sm font-semibold text-amber-600 hover:text-amber-700"
                        rel="noopener noreferrer"
                      >
                        下載音訊
                      </a>
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-slate-500">音訊檔案準備中。</p>
                )}
              </div>
            </article>
          ))}
          </div>
        )}
      </section>
    </div>
  );
}

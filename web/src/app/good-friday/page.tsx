import type { Metadata } from "next";
import Link from "next/link";
import { ChevronRight } from "lucide-react";

import { Breadcrumb } from "@/app/components/common/Breadcrumb";
import { crossSayings, heroContent } from "@/app/good-friday/content";

export const metadata: Metadata = {
  title: "十字架上的七句話 | 達拉斯聖道教會",
  description:
    "受難節特別聚會專題頁面，圍繞主在十字架上的七句話，安靜地思想赦免、應許、託付、承擔、成就與交託。",
};

const primaryButtonClass =
  "inline-flex items-center justify-center rounded-full bg-stone-900 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-stone-700";

const secondaryButtonClass =
  "inline-flex items-center justify-center rounded-full border border-stone-300 bg-white px-6 py-3 text-sm font-semibold text-stone-800 transition-colors hover:border-stone-400 hover:bg-stone-100";

export default function GoodFridayPage() {
  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: heroContent.title },
  ];

  return (
    <div className="bg-stone-50 text-stone-900">
      <Breadcrumb links={breadcrumbLinks} />

      <section className="relative overflow-hidden border-y border-stone-200 bg-[radial-gradient(circle_at_top,_rgba(255,255,255,0.9),_rgba(245,245,244,0.96)_55%,_rgba(231,229,228,0.9)_100%)]">
        <div
          className="absolute inset-0 bg-cover bg-center bg-no-repeat"
          style={{ backgroundImage: `url(${heroContent.heroImageSrc})` }}
        />
        <div className="absolute inset-0 bg-stone-950/50" />
        <div className="absolute inset-0 bg-gradient-to-b from-stone-950/65 via-stone-950/40 to-stone-950/65" />
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-stone-300 to-transparent" />
        <div className="container mx-auto px-6 py-20 md:py-28">
          <div className="mx-auto max-w-3xl text-center">
            <p className="relative text-sm font-semibold uppercase tracking-[0.28em] text-stone-200">
              {heroContent.subtitle}
            </p>
            <h1 className="relative mt-6 font-display text-4xl font-bold tracking-tight text-white md:text-6xl">
              {heroContent.title}
            </h1>
            <div className="relative mt-8 space-y-3 text-base leading-8 text-stone-100 md:text-lg">
              {heroContent.introduction.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="border-b border-stone-200 bg-white">
        <div className="container mx-auto px-6 py-14 md:py-20">
          <div className="mx-auto max-w-3xl">
            <p className="text-lg leading-8 text-stone-700 md:text-xl md:leading-9">
              {heroContent.opening}
            </p>
          </div>
        </div>
      </section>

      <section className="bg-stone-50">
        <div className="container mx-auto px-6 py-8 md:py-14">
          <div className="mx-auto max-w-4xl">
            {crossSayings.map((saying, index) => (
              <section
                key={saying.id}
                className="border-t border-stone-200 py-10 first:border-t-0 first:pt-0 md:py-14"
              >
                <div className="mx-auto max-w-3xl">
                  <p className="text-sm font-semibold tracking-[0.24em] text-stone-500">
                    0{index + 1}
                  </p>
                  <h2 className="mt-3 font-display text-2xl font-bold leading-tight text-stone-950 md:text-3xl">
                    {saying.title}
                  </h2>
                  <blockquote className="mt-6 rounded-3xl border border-stone-200 bg-stone-100/80 px-6 py-5 text-lg font-medium leading-8 text-stone-900 md:px-8 md:text-xl md:leading-9">
                    <span className="block text-sm font-semibold tracking-[0.18em] text-stone-500">
                      {saying.scriptureReference}
                    </span>
                    <p className="mt-3 font-display">「{saying.scripture}」</p>
                  </blockquote>
                  <p className="mt-6 text-base leading-8 text-stone-700 md:text-lg">
                    {saying.body}
                  </p>
                  <p className="mt-6 border-l-2 border-stone-300 pl-5 text-base font-semibold leading-8 text-stone-900 md:text-lg">
                    {saying.takeaway}
                  </p>
                </div>
              </section>
            ))}
          </div>
        </div>
      </section>

      <section className="border-y border-stone-200 bg-white">
        <div className="container mx-auto px-6 py-16 md:py-20">
          <div className="mx-auto max-w-3xl text-center">
            <div className="mx-auto mb-8 h-12 w-px bg-stone-300" />
            <div className="space-y-3 text-lg leading-8 text-stone-700 md:text-xl md:leading-9">
              {heroContent.summary.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="bg-stone-100">
        <div className="container mx-auto px-6 py-16 md:py-20">
          <div className="mx-auto max-w-3xl rounded-[2rem] border border-stone-200 bg-white/80 px-6 py-10 text-center shadow-sm backdrop-blur-sm md:px-12 md:py-14">
            <p className="text-lg leading-8 text-stone-700 md:text-xl md:leading-9">
              {heroContent.cta}
            </p>
            <div className="mt-8 flex flex-col items-stretch justify-center gap-4 sm:flex-row">
              <Link href="/" className={primaryButtonClass}>
                主日聚會
              </Link>
              <Link href="/contact" className={secondaryButtonClass}>
                聯絡我們
                <ChevronRight className="ml-2 h-4 w-4" />
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

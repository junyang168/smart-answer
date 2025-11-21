"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchFullArticleList } from "@/app/admin/full_article/api";
import { FullArticleSummary } from "@/app/types/full-article";

interface FullArticleListProps {
  initialArticles: FullArticleSummary[];
}

type ListState =
  | { status: "idle"; data: FullArticleSummary[] }
  | { status: "loading"; data: FullArticleSummary[] }
  | { status: "ready"; data: FullArticleSummary[] }
  | { status: "error"; data: FullArticleSummary[]; error: string };

const statusBadgeClass: Record<string, string> = {
  draft: "bg-gray-100 text-gray-700",
  generated: "bg-blue-100 text-blue-700",
  final: "bg-emerald-100 text-emerald-700",
};

function formatDate(value: string): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function FullArticleList({ initialArticles }: FullArticleListProps) {
  type SortKey = "updated_at" | "article_type" | "core_bible_verses";

  const [state, setState] = useState<ListState>({ status: "idle", data: initialArticles });
  const [sortKey, setSortKey] = useState<SortKey>("updated_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [filterType, setFilterType] = useState<string>("all");

  useEffect(() => {
    let abort = false;
    const reload = async () => {
      setState((prev) => ({ status: "loading", data: prev.data }));
      try {
        const articles = await fetchFullArticleList();
        if (!abort) {
          setState({ status: "ready", data: articles });
        }
      } catch (error) {
        if (!abort) {
          const message = error instanceof Error ? error.message : "載入失敗";
          setState({ status: "error", data: initialArticles, error: message });
        }
      }
    };

    if (state.status === "idle") {
      reload();
    }

    return () => {
      abort = true;
    };
  }, [state.status, initialArticles]);

  const articles = state.data;

  const { sortedArticles, childrenMap } = useMemo(() => {
    // Apply article type filter first
    const filteredArticles = filterType === "all"
      ? articles
      : articles.filter(article => article.articleType === filterType);

    // First, identify which '講稿素材' articles are referenced by any parent
    const referencedMaterialIds = new Set<string>();
    filteredArticles.forEach(article => {
      if (article.sourceFullArticleIds && article.sourceFullArticleIds.length > 0) {
        article.sourceFullArticleIds.forEach(childId => {
          referencedMaterialIds.add(childId);
        });
      }
    });

    // Build children map: parent ID -> child articles
    const childrenMap = new Map<string, FullArticleSummary[]>();
    filteredArticles.forEach(article => {
      if (article.sourceFullArticleIds && article.sourceFullArticleIds.length > 0) {
        const children: FullArticleSummary[] = [];
        article.sourceFullArticleIds.forEach(childId => {
          // Include children from the full article list, not just filtered ones
          const childArticle = articles.find(a => a.id === childId);
          if (childArticle && childArticle.articleType === '講稿素材') {
            children.push(childArticle);
          }
        });
        if (children.length > 0) {
          childrenMap.set(article.id, children);
        }
      }
    });

    // Filter out referenced '講稿素材' articles from main list
    const parentArticles = filteredArticles.filter(article =>
      !(article.articleType === '講稿素材' && referencedMaterialIds.has(article.id))
    );

    // Sort the parent articles
    const list = [...parentArticles];
    const factor = sortOrder === "asc" ? 1 : -1;
    list.sort((a, b) => {
      if (sortKey === "updated_at") {
        const timeA = new Date(a.updated_at).getTime() || 0;
        const timeB = new Date(b.updated_at).getTime() || 0;
        return factor * (timeA - timeB);
      }
      if (sortKey === "article_type") {
        const typeA = (a.articleType ?? "").toString();
        const typeB = (b.articleType ?? "").toString();
        return factor * typeA.localeCompare(typeB, "zh-Hant");
      }
      const lenA = a.coreBibleVerses ? a.coreBibleVerses.length : 0;
      const lenB = b.coreBibleVerses ? b.coreBibleVerses.length : 0;
      return factor * (lenA - lenB);
    });

    return { sortedArticles: list, childrenMap };
  }, [articles, sortKey, sortOrder, filterType]);

  const handleHeaderSort = useCallback(
    (key: SortKey, defaultOrder: "asc" | "desc" = "asc") => {
      setSortOrder((prevOrder) => {
        if (sortKey === key) {
          return prevOrder === "asc" ? "desc" : "asc";
        }
        return defaultOrder;
      });
      setSortKey(key);
    },
    [sortKey],
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">文章管理</h1>
          <p className="text-gray-600 mt-2">管理由AI產生的完整文章，並可進行人工編輯與審稿。</p>
        </div>
        <div className="flex gap-3 items-center">
          <div className="flex items-center gap-2">
            <label htmlFor="type-filter" className="text-sm font-medium text-gray-700">
              類型：
            </label>
            <select
              id="type-filter"
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
              className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="all">全部</option>
              <option value="釋經">釋經</option>
              <option value="神學觀點">神學觀點</option>
              <option value="短文">短文</option>
              <option value="講稿素材">講稿素材</option>
            </select>
          </div>
          <Link
            href="/admin/full_article/new"
            className="inline-flex items-center px-4 py-2 rounded-md bg-blue-600 text-white font-medium hover:bg-blue-700 transition"
          >
            新增文章
          </Link>
        </div>
      </header>

      {state.status === "error" && (
        <div className="p-4 border border-red-200 bg-red-50 text-red-700 rounded-md">{state.error}</div>
      )}

      <div className="overflow-x-auto bg-white border border-gray-200 rounded-xl shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">標題</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">副標題</th>
              <th
                className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-blue-600"
                onClick={() => handleHeaderSort("article_type")}
              >
                文章類型 {sortKey === "article_type" ? (sortOrder === "asc" ? "▲" : "▼") : ""}
              </th>
              <th
                className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-blue-600"
                onClick={() => handleHeaderSort("core_bible_verses")}
              >
                核心經文 {sortKey === "core_bible_verses" ? (sortOrder === "asc" ? "▲" : "▼") : ""}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">最新狀態</th>
              <th
                className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-blue-600"
                onClick={() => handleHeaderSort("updated_at", "desc")}
              >
                更新時間 {sortKey === "updated_at" ? (sortOrder === "asc" ? "▲" : "▼") : ""}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">建立時間</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {sortedArticles.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-gray-500">
                  尚未建立任何全文文章。
                </td>
              </tr>
            )}
            {sortedArticles.map((article) => {
              const badgeClass = statusBadgeClass[article.status] ?? "bg-gray-100 text-gray-700";
              const children = childrenMap.get(article.id) || [];

              return (
                <>
                  <tr key={article.id} className="hover:bg-blue-50 transition-colors">
                    <td className="px-4 py-3">
                      <Link href={`/admin/full_article/${article.id}`} className="text-blue-600 hover:underline font-medium">
                        {article.name || article.slug}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-gray-600">{article.subtitle || "—"}</td>
                    <td className="px-4 py-3 text-gray-600">{article.articleType ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600">
                      {article.coreBibleVerses && article.coreBibleVerses.length > 0 ? (
                        <div className="space-y-1 text-xs">
                          {article.coreBibleVerses.slice(0, 3).map((verse) => (
                            <p key={verse} className="truncate">
                              {verse}
                            </p>
                          ))}
                          {article.coreBibleVerses.length > 3 ? (
                            <p className="text-gray-400">…等 {article.coreBibleVerses.length} 篇</p>
                          ) : null}
                        </div>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex px-2.5 py-1 rounded-full text-xs font-semibold ${badgeClass}`}>
                        {article.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-600">{formatDate(article.updated_at)}</td>
                    <td className="px-4 py-3 text-gray-600">{formatDate(article.created_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-2">
                        <Link
                          href={`/resources/full_article/${article.id}?nocache=1`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center rounded-md border border-blue-200 px-3 py-1 text-sm text-blue-600 hover:bg-blue-50"
                        >
                          觀看
                        </Link>
                        <Link
                          href={`/admin/full_article/${article.id}`}
                          className="inline-flex items-center rounded-md border border-gray-200 px-3 py-1 text-sm text-gray-700 hover:bg-gray-50"
                        >
                          編輯
                        </Link>
                      </div>
                    </td>
                  </tr>

                  {/* Render nested child articles */}
                  {children.map((child) => {
                    const childBadgeClass = statusBadgeClass[child.status] ?? "bg-gray-100 text-gray-700";
                    return (
                      <tr key={`child-${child.id}`} className="bg-gray-50 hover:bg-gray-100 transition-colors">
                        <td className="px-4 py-3 pl-8">
                          <div className="flex items-center gap-2">
                            <span className="text-gray-400">└─</span>
                            <Link href={`/admin/full_article/${child.id}`} className="text-blue-600 hover:underline font-medium">
                              {child.name || child.slug}
                            </Link>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-gray-600">{child.subtitle || "—"}</td>
                        <td className="px-4 py-3 text-gray-600 text-xs">
                          <span className="inline-flex px-2 py-0.5 rounded bg-amber-100 text-amber-700">
                            {child.articleType ?? "—"}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-gray-600">
                          {child.coreBibleVerses && child.coreBibleVerses.length > 0 ? (
                            <div className="space-y-1 text-xs">
                              {child.coreBibleVerses.slice(0, 3).map((verse) => (
                                <p key={verse} className="truncate">
                                  {verse}
                                </p>
                              ))}
                              {child.coreBibleVerses.length > 3 ? (
                                <p className="text-gray-400">…等 {child.coreBibleVerses.length} 篇</p>
                              ) : null}
                            </div>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex px-2.5 py-1 rounded-full text-xs font-semibold ${childBadgeClass}`}>
                            {child.status}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-gray-600">{formatDate(child.updated_at)}</td>
                        <td className="px-4 py-3 text-gray-600">{formatDate(child.created_at)}</td>
                        <td className="px-4 py-3">
                          <div className="flex gap-2">
                            <Link
                              href={`/resources/full_article/${child.id}?nocache=1`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center rounded-md border border-blue-200 px-3 py-1 text-sm text-blue-600 hover:bg-blue-50"
                            >
                              觀看
                            </Link>
                            <Link
                              href={`/admin/full_article/${child.id}`}
                              className="inline-flex items-center rounded-md border border-gray-200 px-3 py-1 text-sm text-gray-700 hover:bg-gray-50"
                            >
                              編輯
                            </Link>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// components/ai-assistant/ChatSidebar.tsx
"use client";

import Link from 'next/link';
import { Plus, MessageSquare } from 'lucide-react';

interface ChatSidebarProps {
    history: { id: string; title: string }[];
    activeChatId?: string;
    onNewChat: () => void;
}

export const ChatSidebar = ({ history, activeChatId, onNewChat }: ChatSidebarProps) => {
    return (
        <aside className="w-1/4 min-w-[250px] bg-gray-50 border-r flex flex-col">
            {/* 頂部的新建對話按鈕 */}
            <div className="p-4 border-b">
                <button
                    onClick={onNewChat}
                    className="w-full flex items-center justify-center gap-2 bg-blue-600 text-white font-semibold py-2 px-4 rounded-lg hover:bg-blue-700 transition-colors"
                >
                    <Plus className="w-5 h-5" />
                    新建對話
                </button>
            </div>

            {/* 對話歷史列表 */}
            <nav className="flex-1 overflow-y-auto p-2">
                <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider px-2 mb-2">對話歷史</h2>
                <ul className="space-y-1">
                    {history.map(chat => (
                        <li key={chat.id}>
                            <Link
                                href={`/qa/ai-assistant/${chat.id}`}
                                className={`flex items-center gap-3 p-2 rounded-md text-sm transition-colors ${
                                    activeChatId === chat.id
                                        ? 'bg-blue-100 text-blue-800 font-semibold'
                                        : 'text-gray-700 hover:bg-gray-200'
                                }`}
                            >
                                <MessageSquare className="w-4 h-4 flex-shrink-0" />
                                <span className="truncate">{chat.title}</span>
                            </Link>
                        </li>
                    ))}
                </ul>
            </nav>

            {/* (可選) 側邊欄底部，可以放用戶信息等 */}
            <div className="p-4 border-t">
                {/* User profile / Logout button can go here */}
            </div>
        </aside>
    );
};
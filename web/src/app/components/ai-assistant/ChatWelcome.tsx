// components/ai-assistant/ChatWelcome.tsx
"use client";

import { Bot } from 'lucide-react';

export const ChatWelcome = () => {
    return (
        <div className="flex-1 flex flex-col items-center justify-center text-center text-gray-500 p-8">
            <Bot size={64} className="mx-auto mb-6 text-gray-400"/>
            <h2 className="text-3xl font-bold font-display text-gray-800">歡迎使用 AI 信仰助教</h2>
            <p className="mt-4 max-w-md">
                我已學習了王守仁教授的講道內容，請問問題，或者從左側選擇一個歷史對話繼續，或點擊“新建對話”開始一個全新的對話。
            </p>
            <div className="mt-10 p-4 bg-gray-50 rounded-lg border text-sm text-left">
                <h3 className="font-semibold text-gray-700 mb-2">您可以試著問我：</h3>
                <ul className="list-disc list-inside space-y-1">
                    <li>請解釋什麼是“義”？</li>
                    <li>基督徒應該如何看待苦難？</li>
                    <li>總結一下馬太福音24章的主要觀點。</li>
                </ul>
            </div>
        </div>
    );
};
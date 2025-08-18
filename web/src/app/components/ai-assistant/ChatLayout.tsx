// components/ai-assistant/ChatLayout.tsx
"use client";

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { ChatSidebar } from './ChatSidebar';
import { ChatMessages } from './ChatMessages';
import { ChatInput } from './ChatInput';
import { ChatWelcome } from './ChatWelcome';
import { Message } from '@/app/interfaces/message';

// 模擬 API
async function fetchChatHistoryList() {
    return [
        { id: 'chat-1', title: '關於三位一體的問題' },
        { id: 'chat-2', title: '苦難存在的意義' },
    ];
}
async function fetchChatMessages(chatId: string): Promise<Message[]> {
    return [
        { role: 'user', content: '苦難存在的意義是什麼？' },
        { role: 'assistant', content: '這是一個很好的問題...' },
    ];
}

export const ChatLayout = ({ chatId }: { chatId?: string }) => {
    const router = useRouter();
    const [history, setHistory] = useState<{ id: string; title: string }[]>([]);
    const [messages, setMessages] = useState<Message[]>([]);
    const [isLoading, setIsLoading] = useState(false);

    // 加載對話歷史列表
    useEffect(() => {
        fetchChatHistoryList().then(setHistory);
    }, []);

    // 加載選定對話的消息
    useEffect(() => {
        if (chatId) {
            setIsLoading(true);
            fetchChatMessages(chatId).then(setMessages).finally(() => setIsLoading(false));
        } else {
            setMessages([]); // 如果沒有 chatId，則清空消息
        }
    }, [chatId]);

    const handleNewChat = () => {
        router.push('/qa/ai-assistant');
    };

    const handleSendMessage = async (input: string) => {
        const userMessage: Message = { role: 'user', content: input };
        setMessages(prev => [...prev, userMessage]);
        setIsLoading(true);

        // 如果是新對話，先創建對話，獲取 newChatId，然後再發送消息
        let targetChatId = chatId;
        if (!targetChatId) {
            // const newChat = await api.createChat(input); // 調用 POST /api/chats
            const newChatId = `chat-${Date.now()}`; // 模擬返回的 ID
            targetChatId = newChatId;
            // 使用 router.replace 更新 URL 而不觸發頁面刷新
            router.replace(`/qa/ai-assistant/${newChatId}`);
            // 更新側邊欄歷史
            setHistory(prev => [{ id: newChatId, title: input }, ...prev]);
        }
        
        // const response = await api.sendMessage(targetChatId, [...messages, userMessage]);
        const assistantMessage: Message = { role: 'assistant', content: "這是 AI 的回答..." };
        setMessages(prev => [...prev, assistantMessage]);
        setIsLoading(false);
    };

    return (
        <div className="flex h-[80vh] bg-white border rounded-lg shadow-lg">
            {/* 左側邊欄 */}
            <ChatSidebar
                history={history}
                activeChatId={chatId}
                onNewChat={handleNewChat}
            />
            {/* 右側主內容區 */}
            <div className="flex-1 flex flex-col">
                {!chatId && (
                    <ChatWelcome />
                )}
                <ChatMessages messages={messages} isLoading={isLoading} />
                <ChatInput onSendMessage={handleSendMessage} isLoading={isLoading} />
            </div>
        </div>
    );
};
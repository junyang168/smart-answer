// components/ai-assistant/ChatMessages.tsx
"use client";

import { useRef, useEffect } from 'react';
import { Bot, User } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Message } from '@/app/interfaces/message';

interface ChatMessagesProps {
    messages: Message[];
    isLoading: boolean;
}

export const ChatMessages = ({ messages, isLoading }: ChatMessagesProps) => {
    const messagesEndRef = useRef<HTMLDivElement>(null);

    // 每次消息更新或加載狀態變化時，都滾動到底部
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, isLoading]);

    return (
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {messages.map((msg, index) => (
                <div
                    key={index}
                    className={`flex gap-4 items-start ${msg.role === 'user' ? 'justify-end' : ''}`}
                >
                    {/* AI 頭像 */}
                    {msg.role === 'assistant' && (
                        <div className="w-8 h-8 bg-blue-500 rounded-full flex items-center justify-center text-white flex-shrink-0">
                            <Bot size={20} />
                        </div>
                    )}

                    {/* 消息氣泡 */}
                    <div className={`p-4 rounded-lg max-w-xl shadow-sm ${
                        msg.role === 'user'
                            ? 'bg-blue-600 text-white'
                            : 'bg-gray-100 text-gray-800 border'
                    }`}>
                        <article className="prose prose-sm prose-slate dark:prose-invert max-w-none prose-p:my-2 prose-ul:my-2 prose-ol:my-2">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {msg.content}
                            </ReactMarkdown>
                        </article>
                    </div>

                    {/* 用戶頭像 */}
                    {msg.role === 'user' && (
                         <div className="w-8 h-8 bg-gray-300 rounded-full flex items-center justify-center text-gray-600 flex-shrink-0">
                            <User size={20} />
                        </div>
                    )}
                </div>
            ))}

            {/* AI 加載中指示器 */}
            {isLoading && (
                <div className="flex gap-4 items-start">
                    <div className="w-8 h-8 bg-blue-500 rounded-full flex items-center justify-center text-white flex-shrink-0">
                        <Bot size={20} />
                    </div>
                    <div className="p-4 rounded-lg bg-gray-100 text-gray-500 border">
                        <div className="flex items-center gap-2">
                            <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"></span>
                            <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{animationDelay: '0.1s'}}></span>
                            <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{animationDelay: '0.2s'}}></span>
                        </div>
                    </div>
                </div>
            )}

            <div ref={messagesEndRef} />
        </div>
    );
};
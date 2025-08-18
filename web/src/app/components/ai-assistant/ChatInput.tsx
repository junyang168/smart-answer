// components/ai-assistant/ChatInput.tsx
"use client";

import { useState } from 'react';
import { SendHorizonal } from 'lucide-react';

interface ChatInputProps {
    onSendMessage: (input: string) => void;
    isLoading: boolean;
}

export const ChatInput = ({ onSendMessage, isLoading }: ChatInputProps) => {
    const [input, setInput] = useState('');

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (!input.trim() || isLoading) return;
        onSendMessage(input);
        setInput('');
    };

    return (
        <div className="border-t p-4 bg-white">
            <form onSubmit={handleSubmit} className="flex items-center gap-2">
                <textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="請在這裡輸入您的問題... (Shift + Enter 換行)"
                    rows={1}
                    className="flex-1 p-2 border rounded-md resize-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
                    disabled={isLoading}
                    onKeyDown={(e) => {
                        // 按下 Enter 鍵（且沒有按下 Shift 鍵）時觸發提交
                        if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            handleSubmit(e);
                        }
                    }}
                />
                <button
                    type="submit"
                    disabled={isLoading || !input.trim()}
                    className="p-3 bg-blue-600 text-white rounded-full hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
                    aria-label="Send message"
                >
                    <SendHorizonal className="w-5 h-5" />
                </button>
            </form>
        </div>
    );
};
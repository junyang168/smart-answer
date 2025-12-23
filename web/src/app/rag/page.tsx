"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";

type Citation = {
    title: string;
    uri: string;
};

type Message = {
    role: "user" | "model";
    text: string;
    citations?: Citation[];
};

export default function RagChatPage() {
    const [query, setQuery] = useState("");
    const [messages, setMessages] = useState<Message[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [initStatus, setInitStatus] = useState("");

    const handleInit = async () => {
        try {
            setInitStatus("Initializing Corpus...");
            const res = await fetch("/api/rag/init", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ display_name: "Smart Answer Corpus" }),
            });
            const data = await res.json();
            if (res.ok) {
                setInitStatus(`Corpus Created: ${data.corpus_name}. ID: ${data.corpus_id}. Please add RAG_CORPUS_ID to .env`);
            } else {
                setInitStatus(`Error: ${data.detail}`);
            }
        } catch (e) {
            setInitStatus(`Error: ${e}`);
        }
    };

    const handleSync = async () => {
        try {
            setInitStatus("Syncing Drive files...");
            const res = await fetch("/api/rag/sync", { method: "POST" });
            const data = await res.json();
            if (res.ok) {
                setInitStatus("Sync started/completed.");
            } else {
                setInitStatus(`Error: ${data.detail}`);
            }
        } catch (e) {
            setInitStatus(`Error: ${e}`);
        }
    };

    const handleSend = async () => {
        if (!query.trim()) return;

        const userMsg: Message = { role: "user", text: query };
        setMessages((prev) => [...prev, userMsg]);
        setIsLoading(true);
        setQuery("");

        try {
            const res = await fetch("/api/rag/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: userMsg.text }),
            });
            const data = await res.json();

            if (res.ok) {
                const botMsg: Message = {
                    role: "model",
                    text: data.answer,
                    citations: data.citations,
                };
                setMessages((prev) => [...prev, botMsg]);
            } else {
                setMessages((prev) => [
                    ...prev,
                    { role: "model", text: `Error: ${data.detail}` },
                ]);
            }
        } catch (e) {
            setMessages((prev) => [
                ...prev,
                { role: "model", text: `Network Error: ${e}` },
            ]);
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="flex flex-col h-screen p-6 max-w-4xl mx-auto">
            <h1 className="text-3xl font-bold mb-4">Smart Answer Chat (RAG)</h1>

            {/* Admin Controls */}
            <div className="flex gap-4 mb-6 p-4 bg-gray-100 rounded-lg">
                <button
                    onClick={handleInit}
                    className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
                >
                    Initialize Corpus
                </button>
                <button
                    onClick={handleSync}
                    className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700"
                >
                    Sync Drive Files
                </button>
                <button
                    onClick={async () => {
                        try {
                            setInitStatus("Checking status...");
                            const res = await fetch("/api/rag/file_count");
                            const data = await res.json();
                            if (res.ok) {
                                setInitStatus(`Corpus contains ${data.count} files.`);
                            } else {
                                setInitStatus(`Error: ${data.detail}`);
                            }
                        } catch (e) {
                            setInitStatus(`Error: ${e}`);
                        }
                    }}
                    className="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700"
                >
                    Check Status
                </button>
                {initStatus && <span className="text-sm p-2">{initStatus}</span>}
            </div>

            {/* Chat Area */}
            <div className="flex-1 overflow-y-auto mb-4 border rounded-lg p-4 bg-white shadow-sm flex flex-col gap-4">
                {messages.length === 0 && (
                    <div className="text-gray-400 text-center mt-20">
                        Ask a question about the sermons...
                    </div>
                )}
                {messages.map((msg, idx) => (
                    <div
                        key={idx}
                        className={`p-4 rounded-lg max-w-[80%] ${msg.role === "user"
                            ? "bg-blue-100 self-end"
                            : "bg-gray-100 self-start"
                            }`}
                    >
                        <ReactMarkdown className="prose text-sm">{msg.text}</ReactMarkdown>
                        {msg.citations && msg.citations.length > 0 && (
                            <div className="mt-2 pt-2 border-t border-gray-300 text-xs text-gray-600">
                                <strong>Sources:</strong>
                                <ul className="list-disc pl-4 mt-1">
                                    {msg.citations.map((cit, cIdx) => (
                                        <li key={cIdx}>
                                            <a
                                                href={cit.uri}
                                                target="_blank"
                                                rel="noreferrer"
                                                className="text-blue-600 hover:underline"
                                            >
                                                {cit.title || "Document"}
                                            </a>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                    </div>
                ))}
                {isLoading && (
                    <div className="self-start text-gray-500 italic">Thinking...</div>
                )}
            </div>

            {/* Input Area */}
            <div className="flex gap-2">
                <input
                    className="flex-1 border p-3 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSend()}
                    placeholder="Type your question..."
                    disabled={isLoading}
                />
                <button
                    onClick={handleSend}
                    disabled={isLoading || !query.trim()}
                    className="px-6 py-3 bg-blue-600 text-white font-bold rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                    Send
                </button>
            </div>
        </div>
    );
}

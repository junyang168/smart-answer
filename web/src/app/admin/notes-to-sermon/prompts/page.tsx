"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import "easymde/dist/easymde.min.css";

const SimpleMDE = dynamic(() => import("react-simplemde-editor"), {
    ssr: false,
});

interface Prompt {
    id: string;
    name: string;
    content: string;
    is_default: boolean;
    temperature: number;
}

export default function PromptManagerPage() {
    const [prompts, setPrompts] = useState<Prompt[]>([]);
    const [selectedPrompt, setSelectedPrompt] = useState<Prompt | null>(null);
    const [isEditing, setIsEditing] = useState(false);

    // Edit Form State
    const [editName, setEditName] = useState("");
    const [editContent, setEditContent] = useState("");
    const [editTemperature, setEditTemperature] = useState(0.7);

    // Load Prompts
    useEffect(() => {
        fetchPrompts();
    }, []);

    const fetchPrompts = async () => {
        const res = await fetch("/api/admin/notes-to-sermon/prompts");
        const data = await res.json();
        setPrompts(data);
    };

    const handleSelectPrompt = (prompt: Prompt) => {
        setSelectedPrompt(prompt);
        setEditName(prompt.name);
        setEditContent(prompt.content);
        setEditTemperature(prompt.temperature || 0.7);
        setIsEditing(false); // Start in view mode
    };

    const handleCreateNew = () => {
        setSelectedPrompt(null);
        setEditName("New Prompt");
        setEditContent("");
        setEditTemperature(0.7);
        setIsEditing(true);
    };

    const handleSave = async () => {
        if (!editName) return alert("Name is required");
        if (!editContent) return alert("Content is required");

        const payload = { name: editName, content: editContent, temperature: editTemperature };

        try {
            if (selectedPrompt && selectedPrompt.id) {
                // Update
                const res = await fetch(`/api/admin/notes-to-sermon/prompts/${selectedPrompt.id}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    const updated = await res.json();
                    setPrompts(prompts.map(p => p.id === updated.id ? updated : p));
                    setSelectedPrompt(updated);
                    setIsEditing(false);
                    alert("Saved!");
                } else {
                    alert("Failed to update");
                }
            } else {
                // Create
                const res = await fetch(`/api/admin/notes-to-sermon/prompts`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    const created = await res.json();
                    setPrompts([...prompts, created]);
                    setSelectedPrompt(created);
                    setIsEditing(false);
                    alert("Created!");
                } else {
                    alert("Failed to create");
                }
            }
        } catch (e) {
            alert("Error saving prompt");
        }
    };

    const handleDelete = async () => {
        if (!selectedPrompt) return;
        if (selectedPrompt.is_default) return alert("Cannot delete default prompt");

        if (!confirm(`Delete prompt "${selectedPrompt.name}"?`)) return;

        try {
            const res = await fetch(`/api/admin/notes-to-sermon/prompts/${selectedPrompt.id}`, {
                method: "DELETE"
            });
            if (res.ok) {
                setPrompts(prompts.filter(p => p.id !== selectedPrompt.id));
                setSelectedPrompt(null);
                setIsEditing(false);
                alert("Deleted");
            } else {
                alert("Failed to delete");
            }
        } catch (e) {
            alert("Error deleting prompt");
        }
    };

    // Memoized options for SimpleMDE
    const editorOptions = React.useMemo(() => ({
        status: false,
        spellChecker: false,
        minHeight: "500px"
    }), []);

    return (
        <div className="container mx-auto p-6 flex flex-col h-screen max-h-screen overflow-hidden">
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center space-x-4">
                    <Link href="/admin/notes-to-sermon/projects" className="text-gray-500 hover:text-gray-700 text-sm">
                        &larr; Back to Projects
                    </Link>
                    <h1 className="text-2xl font-bold">Manage Prompts</h1>
                </div>
                <button
                    onClick={handleCreateNew}
                    className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700"
                >
                    + New Prompt
                </button>
            </div>

            <div className="flex flex-1 border rounded overflow-hidden">
                {/* Sidebar List */}
                <div className="w-1/3 bg-gray-50 border-r overflow-y-auto">
                    {prompts.map(p => (
                        <div
                            key={p.id}
                            className={`p-4 border-b cursor-pointer hover:bg-white ${selectedPrompt?.id === p.id ? 'bg-white border-l-4 border-l-indigo-600' : ''}`}
                            onClick={() => handleSelectPrompt(p)}
                        >
                            <div className="font-bold text-gray-800">{p.name}</div>
                            {p.is_default && <span className="text-xs bg-gray-200 px-2 py-0.5 rounded text-gray-600">Default</span>}
                            <div className="text-xs text-gray-500 mt-1 truncate">{p.content.substring(0, 50)}...</div>
                        </div>
                    ))}
                    {prompts.length === 0 && <div className="p-4 text-gray-500 text-center">Loading...</div>}
                </div>

                {/* Editor Area */}
                <div className="w-2/3 bg-white flex flex-col">
                    {selectedPrompt || isEditing ? (
                        <div className="flex flex-col h-full">
                            <div className="p-4 border-b flex justify-between items-center bg-gray-50">
                                {isEditing ? (
                                    <div className="flex items-center flex-1 mr-4">
                                        <input
                                            type="text"
                                            className="border p-2 rounded w-full"
                                            value={editName}
                                            onChange={e => setEditName(e.target.value)}
                                            placeholder="Prompt Name"
                                        />
                                        <div className="flex items-center space-x-2 bg-white px-2 rounded border ml-2 whitespace-nowrap">
                                            <label className="text-xs text-gray-500">Temp:</label>
                                            <input
                                                type="number"
                                                step="0.1"
                                                min="0.0"
                                                max="1.0"
                                                className="w-16 p-1 text-sm outline-none"
                                                value={editTemperature}
                                                onChange={e => setEditTemperature(parseFloat(e.target.value))}
                                            />
                                        </div>
                                    </div>
                                ) : (
                                    <div className="flex items-center space-x-3">
                                        <h2 className="text-xl font-bold">{selectedPrompt?.name}</h2>
                                        <span className="text-xs bg-blue-100 text-blue-800 px-2 py-1 rounded">
                                            Temp: {selectedPrompt?.temperature}
                                        </span>
                                    </div>
                                )}

                                <div className="space-x-2">
                                    {!isEditing && (
                                        <>
                                            <button
                                                onClick={() => { setIsEditing(true); }}
                                                className="text-indigo-600 hover:text-indigo-800 px-3 py-1 rounded border border-indigo-200"
                                            >
                                                Edit
                                            </button>
                                            {!selectedPrompt?.is_default && (
                                                <button
                                                    onClick={handleDelete}
                                                    className="text-red-600 hover:text-red-800 px-3 py-1 rounded border border-red-200"
                                                >
                                                    Delete
                                                </button>
                                            )}
                                        </>
                                    )}
                                    {isEditing && (
                                        <>
                                            <button
                                                onClick={() => {
                                                    if (selectedPrompt) {
                                                        handleSelectPrompt(selectedPrompt); // Reset
                                                    } else {
                                                        setSelectedPrompt(null);
                                                        setIsEditing(false);
                                                    }
                                                }}
                                                className="text-gray-600 px-3 py-1"
                                            >
                                                Cancel
                                            </button>
                                            <button
                                                onClick={handleSave}
                                                className="bg-indigo-600 text-white px-4 py-1 rounded hover:bg-indigo-700"
                                            >
                                                Save
                                            </button>
                                        </>
                                    )}
                                </div>
                            </div>
                            <div className="flex-1 overflow-auto p-0">
                                {isEditing ? (
                                    <SimpleMDE
                                        value={editContent}
                                        onChange={setEditContent}
                                        className="h-full"
                                        options={editorOptions}
                                    />
                                ) : (
                                    <div className="p-6 prose max-w-none whitespace-pre-wrap font-mono text-sm bg-gray-50 h-full overflow-auto">
                                        {selectedPrompt?.content}
                                    </div>
                                )}
                            </div>
                        </div>
                    ) : (
                        <div className="flex items-center justify-center h-full text-gray-400">
                            Select a prompt to view or edit
                        </div>
                    )}
                </div>
            </div>
        </div >
    );
}

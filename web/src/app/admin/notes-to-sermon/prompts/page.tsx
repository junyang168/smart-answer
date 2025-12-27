"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import "easymde/dist/easymde.min.css";

const SimpleMDE = dynamic(() => import("react-simplemde-editor"), {
    ssr: false,
});

// Types
interface Prompt {
    id: string;
    name: string;
    content: string;
    is_default: boolean;
    temperature: number;
    role: string;
}

const ROLES = ["drafter", "exegete", "theologian", "illustrator", "critic", "structuring_specialist"];
const ROLE_COLORS: Record<string, string> = {
    drafter: "bg-blue-100 text-blue-800",
    exegete: "bg-purple-100 text-purple-800",
    theologian: "bg-yellow-100 text-yellow-800",
    illustrator: "bg-pink-100 text-pink-800",
    critic: "bg-red-100 text-red-800",
    structuring_specialist: "bg-gray-100 text-gray-800"
};

export default function PromptManagerPage() {
    const [prompts, setPrompts] = useState<Prompt[]>([]);
    const [selectedPrompt, setSelectedPrompt] = useState<Prompt | null>(null);
    const [isEditing, setIsEditing] = useState(false);

    // Edit Form State
    const [editName, setEditName] = useState("");
    const [editContent, setEditContent] = useState("");
    const [editTemperature, setEditTemperature] = useState(0.7);
    const [editRole, setEditRole] = useState("drafter");

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
        setEditRole(prompt.role || "drafter");
        setIsEditing(false); // Start in view mode
    };

    const handleCreateNew = () => {
        setSelectedPrompt(null);
        setEditName("New Prompt");
        setEditContent("");
        setEditTemperature(0.7);
        setEditRole("drafter");
        setIsEditing(true);
    };

    const handleSave = async () => {
        if (!editName) return alert("Name is required");
        if (!editContent) return alert("Content is required");

        const payload = { name: editName, content: editContent, temperature: editTemperature, role: editRole };

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
        // Allow deleting any prompt if the user really wants to, backend handles logic if needed.
        const message = selectedPrompt.is_default
            ? `Reset "${selectedPrompt.name}" to Factory Default?\n\nThis will discard your custom edits and restore the system default.`
            : `Delete prompt "${selectedPrompt.name}"?`;

        if (!confirm(message)) return;

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

    // Group prompts by role
    const promptsByRole = ROLES.reduce((acc, role) => {
        acc[role] = prompts.filter(p => (p.role || "drafter") === role);
        return acc;
    }, {} as Record<string, Prompt[]>);

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
                    {ROLES.map(role => (
                        <div key={role}>
                            {promptsByRole[role]?.length > 0 && (
                                <>
                                    <div className="px-4 py-2 bg-gray-100 font-semibold text-xs text-gray-500 uppercase tracking-wider sticky top-0">
                                        {role}
                                    </div>
                                    {promptsByRole[role].map(p => (
                                        <div
                                            key={p.id}
                                            className={`p-4 border-b cursor-pointer hover:bg-white ${selectedPrompt?.id === p.id ? 'bg-white border-l-4 border-l-indigo-600' : ''}`}
                                            onClick={() => handleSelectPrompt(p)}
                                        >
                                            <div className="font-bold text-gray-800">{p.name}</div>
                                            <div className="flex items-center space-x-2 mt-1">
                                                <span className={`text-[10px] px-1.5 py-0.5 rounded ${ROLE_COLORS[role] || "bg-gray-200"}`}>
                                                    {role}
                                                </span>
                                                {p.is_default && <span className="text-[10px] bg-gray-200 px-1.5 py-0.5 rounded text-gray-600">Default</span>}
                                            </div>
                                            <div className="text-xs text-gray-500 mt-1 truncate">{p.content.substring(0, 50)}...</div>
                                        </div>
                                    ))}
                                </>
                            )}
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
                                    <div className="flex items-center flex-1 mr-4 space-x-2">
                                        <input
                                            type="text"
                                            className="border p-2 rounded flex-1"
                                            value={editName}
                                            onChange={e => setEditName(e.target.value)}
                                            placeholder="Prompt Name"
                                        />
                                        <select
                                            className="border p-2 rounded text-sm bg-white"
                                            value={editRole}
                                            onChange={e => setEditRole(e.target.value)}
                                        >
                                            {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                                        </select>
                                        <div className="flex items-center space-x-1 bg-white px-2 py-1 rounded border whitespace-nowrap">
                                            <label className="text-xs text-gray-500">Temp:</label>
                                            <input
                                                type="number"
                                                step="0.1"
                                                min="0.0"
                                                max="1.0"
                                                className="w-12 p-1 text-sm outline-none"
                                                value={editTemperature}
                                                onChange={e => setEditTemperature(parseFloat(e.target.value))}
                                            />
                                        </div>
                                    </div>
                                ) : (
                                    <div className="flex items-center space-x-3">
                                        <h2 className="text-xl font-bold">{selectedPrompt?.name}</h2>
                                        <span className={`text-xs px-2 py-1 rounded ${ROLE_COLORS[selectedPrompt?.role || "drafter"]}`}>
                                            {selectedPrompt?.role}
                                        </span>
                                        <span className="text-xs bg-blue-50 text-blue-600 px-2 py-1 rounded border border-blue-100">
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
                                            <button
                                                onClick={handleDelete}
                                                className={`px-3 py-1 rounded border ${selectedPrompt?.is_default
                                                    ? "text-orange-600 hover:text-orange-800 border-orange-200"
                                                    : "text-red-600 hover:text-red-800 border-red-200"
                                                    }`}
                                            >
                                                {selectedPrompt?.is_default ? "Reset to Default" : "Delete"}
                                            </button>
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

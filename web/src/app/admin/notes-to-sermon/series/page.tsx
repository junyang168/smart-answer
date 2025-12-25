"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

interface LectureSeries {
    id: string;
    title: string;
    description?: string;
    created_at: string;
    updated_at: string;
    lectures: any[];
}

export default function SeriesDashboardPage() {
    const [seriesList, setSeriesList] = useState<LectureSeries[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [showCreateForm, setShowCreateForm] = useState(false);
    const [newTitle, setNewTitle] = useState("");
    const [newDesc, setNewDesc] = useState("");
    const router = useRouter();

    useEffect(() => {
        fetchSeries();
    }, []);

    const fetchSeries = async () => {
        try {
            const res = await fetch("/api/admin/notes-to-sermon/series");
            if (res.ok) {
                const data = await res.json();
                setSeriesList(data);
            }
        } catch (e) {
            console.error("Failed to fetch series", e);
        } finally {
            setIsLoading(false);
        }
    };

    const handleCreate = async () => {
        if (!newTitle) return alert("Title is required");
        try {
            const res = await fetch("/api/admin/notes-to-sermon/series", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: newTitle, description: newDesc }),
            });
            if (res.ok) {
                const created = await res.json();
                setSeriesList([...seriesList, created]);
                setShowCreateForm(false);
                setNewTitle("");
                setNewDesc("");
                alert("Series created!");
                // Optionally redirect to detail page
                // router.push(`/admin/notes-to-sermon/series/${created.id}`);
            } else {
                alert("Failed to create series");
            }
        } catch (e) {
            alert("Error creating series");
        }
    };

    const handleDelete = async (id: string, e: React.MouseEvent) => {
        e.preventDefault(); // Prevent navigation if button is inside link (though it shouldn't be)
        if (!confirm("Are you sure you want to delete this series? All lectures inside it will be lost.")) return;

        try {
            const res = await fetch(`/api/admin/notes-to-sermon/series/${id}`, {
                method: "DELETE",
            });
            if (res.ok) {
                setSeriesList(seriesList.filter(s => s.id !== id));
            } else {
                alert("Failed to delete");
            }
        } catch (e) {
            alert("Error deleting");
        }
    };

    return (
        <div className="container mx-auto p-6">
            <div className="flex items-center justify-between mb-6">
                <div className="flex items-center space-x-4">
                    <Link href="/admin/notes-to-sermon/projects" className="text-gray-500 hover:text-gray-700 text-sm">
                        &larr; Projects
                    </Link>
                    <h1 className="text-2xl font-bold">Lecture Series</h1>
                </div>
                <button
                    onClick={() => setShowCreateForm(true)}
                    className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700 shadow"
                >
                    + New Series
                </button>
            </div>

            {showCreateForm && (
                <div className="bg-white p-6 rounded-lg shadow-md border mb-8 animate-fade-in-down">
                    <h2 className="text-lg font-semibold mb-4">Create New Series</h2>
                    <div className="grid grid-cols-1 gap-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700">Title</label>
                            <input
                                type="text"
                                className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm p-2"
                                value={newTitle}
                                onChange={e => setNewTitle(e.target.value)}
                                placeholder="e.g. Matthew Gospel Expository"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700">Description</label>
                            <textarea
                                className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm p-2"
                                rows={3}
                                value={newDesc}
                                onChange={e => setNewDesc(e.target.value)}
                                placeholder="Goal description..."
                            />
                        </div>
                        <div className="flex justify-end space-x-3 mt-2">
                            <button
                                onClick={() => setShowCreateForm(false)}
                                className="text-gray-600 px-4 py-2 hover:bg-gray-100 rounded"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleCreate}
                                className="bg-indigo-600 text-white px-4 py-2 rounded hover:bg-indigo-700"
                            >
                                Create
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {isLoading ? (
                <div className="text-center text-gray-500 py-10">Loading...</div>
            ) : seriesList.length === 0 ? (
                <div className="text-center text-gray-500 py-10 border rounded-lg bg-gray-50">
                    No series found. Create one to get started!
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {seriesList.map(item => (
                        <div
                            key={item.id}
                            className="bg-white rounded-lg shadow border hover:shadow-lg transition-shadow cursor-pointer flex flex-col h-full"
                        >
                            <Link href={`/admin/notes-to-sermon/series/${item.id}`} className="flex-1 p-6">
                                <h3 className="text-xl font-bold text-gray-900 mb-2">{item.title}</h3>
                                <p className="text-gray-500 text-sm mb-4 line-clamp-2">
                                    {item.description || "No description"}
                                </p>
                                <div className="flex items-center justify-between text-xs text-gray-400 mt-auto">
                                    <span>{item.lectures.length} Lectures</span>
                                    <span>Updated: {new Date(item.updated_at).toLocaleDateString()}</span>
                                </div>
                            </Link>
                            <div className="border-t p-3 bg-gray-50 flex justify-end">
                                <button
                                    onClick={(e) => handleDelete(item.id, e)}
                                    className="text-red-500 hover:text-red-700 text-sm px-2 py-1"
                                >
                                    Delete
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

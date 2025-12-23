"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";

interface NoteImage {
    filename: string;
    path: string;
    processed: boolean;
}

export default function ProjectCreationPage() {
    const [images, setImages] = useState<NoteImage[]>([]);
    const [projects, setProjects] = useState<any[]>([]);
    const [selectedImages, setSelectedImages] = useState<Set<string>>(new Set());
    const [title, setTitle] = useState("");
    const router = useRouter();

    useEffect(() => {
        fetch("/api/admin/notes-to-sermon/images")
            .then((res) => res.json())
            .then((data) => setImages(data));

        fetch("/api/admin/notes-to-sermon/sermon-projects")
            .then(res => res.json())
            .then(data => setProjects(data));
    }, []);

    const toggleImage = (filename: string) => {
        const newSet = new Set(selectedImages);
        if (newSet.has(filename)) {
            newSet.delete(filename);
        } else {
            newSet.add(filename);
        }
        setSelectedImages(newSet);
    };

    const createProject = async () => {
        if (!title) return alert("Please enter a title");
        if (selectedImages.size === 0) return alert("Please select at least one page");

        const sortedPages = Array.from(selectedImages).sort(); // Basic sort, could be drag-drop later

        try {
            const res = await fetch("/api/admin/notes-to-sermon/sermon-project", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title, pages: sortedPages })
            });
            const project = await res.json();
            // Redirect to the Unified Lab (will be created next)
            router.push(`/admin/notes-to-sermon/project/${project.id}`);
        } catch (e) {
            alert("Failed to create project");
        }
    };

    return (
        <div className="container mx-auto p-6 max-w-4xl">
            <h1 className="text-3xl font-bold mb-6">Sermon Projects</h1>

            {/* Project List */}
            <div className="mb-10">
                <h2 className="text-xl font-semibold mb-4">Existing Projects</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {projects.map(p => (
                        <div
                            key={p.id}
                            className="bg-white p-4 rounded shadow border hover:shadow-md cursor-pointer flex justify-between items-center"
                            onClick={() => router.push(`/admin/notes-to-sermon/project/${p.id}`)}
                        >
                            <div>
                                <h3 className="font-bold text-lg">{p.title}</h3>
                                <span className="text-gray-500 text-sm">{p.pages.length} pages</span>
                            </div>
                            <span className="text-indigo-600">Open &rarr;</span>
                        </div>
                    ))}
                    {projects.length === 0 && <p className="text-gray-500">No projects yet.</p>}
                </div>
            </div>

            <hr className="my-8" />

            <h2 className="text-2xl font-bold mb-6">Create New Project</h2>
            <div className="mb-6 p-4 bg-white rounded shadow">
                <label className="block text-sm font-medium text-gray-700 mb-2">Sermon Title</label>
                <input
                    type="text"
                    className="w-full p-2 border rounded"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="e.g. Sermon on the Mount: The Beatitudes"
                />
            </div>

            <div className="mb-6">
                <h2 className="text-xl font-semibold mb-3">Select Scan Pages</h2>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {images.map(img => (
                        <div
                            key={img.filename}
                            className={`border rounded p-2 cursor-pointer transition ${selectedImages.has(img.filename) ? 'ring-2 ring-indigo-500 bg-indigo-50' : 'hover:bg-gray-50'}`}
                            onClick={() => toggleImage(img.filename)}
                        >
                            {/* Thumbnail Placeholder */}
                            {/* Thumbnail Replaced with Real Image */}
                            <div className="aspect-[3/4] bg-gray-200 mb-2 overflow-hidden">
                                <img
                                    src={`/api/admin/notes-to-sermon/image/${img.filename}`}
                                    alt={img.filename}
                                    className="w-full h-full object-cover"
                                    loading="lazy"
                                />
                            </div>
                            <div className="flex justify-between items-center text-sm">
                                <span className="truncate">{img.filename}</span>
                                <input
                                    type="checkbox"
                                    checked={selectedImages.has(img.filename)}
                                    readOnly
                                />
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            <button
                onClick={createProject}
                className="w-full py-3 bg-green-600 text-white rounded font-bold hover:bg-green-700"
            >
                Create Project
            </button>
        </div>
    );
}

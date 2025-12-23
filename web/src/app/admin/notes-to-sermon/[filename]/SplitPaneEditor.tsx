"use client";

import React, { useState, useEffect } from "react";
// We can use a simple textarea for now or a library if we want to add dependencies
// Since 'react-simplemde-editor' is in package.json, let's try to use it if simple, 
// otherwise standard textarea is safer for V1 to avoid hydration issues without dynamic import.
// Let's stick to Textarea for maximum stability in this V1.

interface Segment {
    id: string;
    raw_text: string;
    refined_text: string;
}

export default function SplitPaneEditor({ filename }: { filename: string }) {
    const [segments, setSegments] = useState<Segment[]>([]);
    const [loading, setLoading] = useState(true);
    const [imageUrl, setImageUrl] = useState<string>("");

    // Load Data
    useEffect(() => {
        // 1. Set Image URL using the new endpoint
        setImageUrl(`/api/admin/notes-to-sermon/image/${filename}`);

        // 2. Fetch Segments
        fetch(`/api/admin/notes-to-sermon/page/${filename}/segments`)
            .then(res => res.json())
            .then(data => {
                setSegments(data);
                setLoading(false);
            })
            .catch(err => {
                console.error("Failed to fetch segments", err);
                setLoading(false);
            });
    }, [filename]);

    if (loading) return <div>Loading Lab...</div>;

    return (
        <div className="flex h-full">
            {/* Left Pane: Image Viewer */}
            <div className="w-1/2 bg-gray-100 border-r overflow-auto p-4 flex justify-center">
                <div className="relative border shadow-lg bg-white min-h-[800px] w-full max-w-[800px]">
                    {imageUrl ? (
                        <img src={imageUrl} alt="Source Note" className="w-full h-auto" />
                    ) : (
                        <p className="text-center p-10 text-gray-400">Loading Image...</p>
                    )}
                </div>
            </div>

            {/* Right Pane: Segment Editor */}
            <div className="w-1/2 bg-white overflow-auto p-4">
                <div className="space-y-6">
                    {segments.map((seg, idx) => (
                        <div key={seg.id} className="border rounded-lg shadow-sm">
                            <div className="bg-gray-50 px-4 py-2 border-b flex justify-between">
                                <span className="font-mono text-xs text-gray-500">Segment {idx + 1}</span>
                                <div className="space-x-2">
                                    <button className="text-xs text-blue-600 hover:underline">Merge Down</button>
                                    <button className="text-xs text-red-600 hover:underline">Split</button>
                                </div>
                            </div>
                            <div className="p-2">
                                <textarea
                                    className="w-full h-32 p-2 border rounded font-mono text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                                    value={seg.refined_text}
                                    onChange={(e) => {
                                        const newSegs = [...segments];
                                        newSegs[idx].refined_text = e.target.value;
                                        setSegments(newSegs);
                                    }}
                                />
                            </div>
                        </div>
                    ))}

                    <button className="w-full py-4 border-2 border-dashed border-gray-300 text-gray-400 rounded hover:border-indigo-500 hover:text-indigo-500 transition">
                        + Add Manual Segment
                    </button>
                </div>
            </div>
        </div>
    );
}

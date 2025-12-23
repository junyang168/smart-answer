"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";

interface NoteImage {
    filename: string;
    path: string;
    processed: boolean;
}

export default function NotesToSermonPage() {
    const [images, setImages] = useState<NoteImage[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetch("/api/admin/notes-to-sermon/images")
            .then((res) => res.json())
            .then((data) => {
                setImages(data);
                setLoading(false);
            })
            .catch((err) => {
                console.error("Failed to fetch images", err);
                setLoading(false);
            });
    }, []);

    const handleProcess = async (filename: string) => {
        try {
            await fetch(`/api/admin/notes-to-sermon/page/${filename}/process`, {
                method: 'POST'
            });
            alert(`Started processing ${filename}`);
            // Optimistic update or refetch
            setImages(prev => prev.map(img => img.filename === filename ? { ...img, processed: true } : img));
        } catch (e) {
            alert("Error starting process");
        }
    }

    return (
        <div className="container mx-auto p-6">
            <h1 className="text-3xl font-bold mb-6">Sermon Conversion Lab</h1>

            {loading ? (
                <p>Loading source images...</p>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {images.map((img) => (
                        <div key={img.filename} className="border rounded-lg p-4 shadow-sm hover:shadow-md transition bg-white">
                            <div className="flex justify-between items-start mb-4">
                                <h3 className="font-semibold truncate" title={img.filename}>{img.filename}</h3>
                                {img.processed ? (
                                    <span className="bg-green-100 text-green-800 text-xs px-2 py-1 rounded">Processed</span>
                                ) : (
                                    <span className="bg-gray-100 text-gray-800 text-xs px-2 py-1 rounded">Raw</span>
                                )}
                            </div>

                            <div className="flex gap-2 mt-4">
                                {/* View/Edit Button */}
                                <Link
                                    href={`/admin/notes-to-sermon/${img.filename}`}
                                    className={`flex-1 text-center py-2 px-4 rounded text-sm ${img.processed ? 'bg-blue-600 text-white hover:bg-blue-700' : 'bg-gray-300 text-gray-500 cursor-not-allowed'}`}
                                    // simplified logic: if not processed, can't view details (or maybe prompt to process)
                                    onClick={(e) => !img.processed && e.preventDefault()}
                                >
                                    {img.processed ? "Open Lab" : "Needs Processing"}
                                </Link>

                                {/* Process Action */}
                                {!img.processed && (
                                    <button
                                        onClick={() => handleProcess(img.filename)}
                                        className="bg-indigo-600 text-white px-4 py-2 rounded text-sm hover:bg-indigo-700"
                                    >
                                        Process
                                    </button>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

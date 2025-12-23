"use client";

import React from "react";
import SplitPaneEditor from "./SplitPaneEditor";

export default function Page({ params }: { params: { filename: string } }) {
    // Decode filename just in case
    const filename = decodeURIComponent(params.filename);

    return (
        <div className="h-[calc(100vh-64px)] flex flex-col">
            <div className="border-b p-4 bg-white flex justify-between items-center">
                <h1 className="text-xl font-bold">Sermon Lab: {filename}</h1>
                <div>
                    {/* Toolbar placeholders */}
                    <span className="text-sm text-gray-500 mr-4">Status: Draft</span>
                    <button className="bg-indigo-600 text-white px-3 py-1 rounded text-sm">Save & Generate</button>
                </div>
            </div>

            <div className="flex-1 overflow-hidden relative">
                <SplitPaneEditor filename={filename} />
            </div>
        </div>
    );
}

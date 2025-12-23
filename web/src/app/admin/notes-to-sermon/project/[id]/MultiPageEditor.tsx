"use client";

import React, { useState, useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import "easymde/dist/easymde.min.css";

import AiCommandPanel from "./AiCommandPanel";

const SimpleMDE = dynamic(() => import("react-simplemde-editor"), {
    ssr: false,
});

export default function MultiPageEditor({ projectId }: { projectId: string }) {
    const [viewMode, setViewMode] = useState<'source' | 'draft'>('source');
    const [isGenerating, setIsGenerating] = useState(false);
    const [projectTitle, setProjectTitle] = useState(projectId);

    // Content State
    const [markdown, setMarkdown] = useState("");
    const [originalMarkdown, setOriginalMarkdown] = useState(""); // Track for dirty check
    const [images, setImages] = useState<string[]>([]);
    const [allImages, setAllImages] = useState<string[]>([]); // For adding new pages
    const [loading, setLoading] = useState(true);
    const [isProcessing, setIsProcessing] = useState(false);
    const [showAdd, setShowAdd] = useState(false);
    const [editorInstance, setEditorInstance] = useState<any>(null);

    // AI Refinement State
    const [selectedText, setSelectedText] = useState("");
    const [isRefining, setIsRefining] = useState(false);
    const [selectionRange, setSelectionRange] = useState<any>(null);


    const editorOptions = useMemo(() => ({
        spellChecker: false,
        status: false,

        placeholder: viewMode === 'source' ? "Unified manuscript will appear here..." : "Generated Draft will appear here...",
        minHeight: "500px",
    }), [viewMode]);

    // Handle Selection changes
    useEffect(() => {
        if (!editorInstance) return;
        const cm = editorInstance.codemirror;

        const handleSelection = () => {
            if (viewMode !== 'draft') {
                setSelectedText("");
                setSelectionRange(null);
                return;
            }

            const selection = cm.getSelection();
            if (selection) {
                setSelectedText(selection);
                // Get range
                const from = cm.getCursor("from");
                const to = cm.getCursor("to");
                setSelectionRange({ from, to });
            } else {
                setSelectedText("");
                setSelectionRange(null);
            }
        };

        cm.on("cursorActivity", handleSelection);
        return () => {
            cm.off("cursorActivity", handleSelection);
        };
    }, [editorInstance, viewMode]);

    // Sync: Markdown -> Image
    useEffect(() => {
        if (!editorInstance) return;
        const cm = editorInstance.codemirror;

        const handleCursor = () => {
            const cursor = cm.getCursor();
            const lineContent = cm.getLine(cursor.line);
            // Check for <!-- Page: filename -->
            const match = lineContent.match(/<!-- Page: (.+?) -->/);
            // Also check previous line just in case user is typing right below it
            if (match) {
                const filename = match[1].replace(" (Not Processed)", ""); // handle suffix if present
                const imgEl = document.getElementById(`page-${filename}`);
                if (imgEl) {
                    imgEl.scrollIntoView({ behavior: "smooth", block: "start" });
                }
            }
        };

        cm.on("cursorActivity", handleCursor);
        return () => {
            cm.off("cursorActivity", handleCursor);
        };
    }, [editorInstance]);

    // Sync: Image -> Markdown
    const scrollToMarkdown = (filename: string) => {
        if (!editorInstance) return;
        const cm = editorInstance.codemirror;
        const fileMarker = `<!-- Page: ${filename}`; // match partial to catch (Not Processed)

        // Find line number
        const lineCount = cm.lineCount();
        for (let i = 0; i < lineCount; i++) {
            const line = cm.getLine(i);
            if (line.includes(fileMarker)) {
                // Scroll to line
                cm.scrollIntoView({ line: i, ch: 0 }, 200); // 200px margin
                cm.setCursor({ line: i, ch: 0 });
                cm.focus();
                break;
            }
        }
    };

    useEffect(() => {
        const load = async () => {
            try {
                // Fetch Content based on mode
                const endpoint = viewMode === 'source' ? 'source' : 'draft';
                const srcRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/${endpoint}`);
                if (srcRes.ok) {
                    const srcData = await srcRes.json();
                    const content = srcData.content || "";
                    setMarkdown(content);
                    setOriginalMarkdown(content);
                } else {
                    setMarkdown("");
                    setOriginalMarkdown("");
                }

                // Fetch Images List
                const imgRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/images`);
                const imgData = await imgRes.json();
                setImages(imgData);

                // Fetch All Available Images (for adding)
                const allRes = await fetch("/api/admin/notes-to-sermon/images");
                const allData = await allRes.json();
                setAllImages(allData.map((d: any) => d.filename));

                // Check if processing
                const metaRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}`);
                const metaData = await metaRes.json();
                if (metaData.title) {
                    setProjectTitle(metaData.title);
                }
                if (metaData.processing) {
                    setIsProcessing(true);
                }

                setLoading(false);
            } catch (e) {
                alert("Error loading project");
            }
        };
        load();
    }, [projectId, viewMode]);

    // Polling for status
    useEffect(() => {
        let interval: NodeJS.Timeout;
        if (isProcessing) {
            interval = setInterval(async () => {
                const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}`);
                const data = await res.json();
                if (!data.processing) {
                    setIsProcessing(false);
                    // Re-fetch content based on current viewMode
                    const endpoint = viewMode === 'source' ? 'source' : 'draft';
                    const srcRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/${endpoint}`);
                    const srcData = await srcRes.json();

                    const content = srcData.content || "";
                    setMarkdown(content);
                    setOriginalMarkdown(content);

                    alert("Processing Complete!");
                }
            }, 3000);
        }
        return () => clearInterval(interval);
    }, [isProcessing, projectId]);

    const handleOCR = async (filename: string) => {
        const confirm = window.confirm(`Start OCR for ${filename}? This might take a few seconds.`);
        if (!confirm) return;

        try {
            await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/ocr`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ filename })
            });
            // Re-fetch source
            const srcRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/source`);
            const srcData = await srcRes.json();
            setMarkdown(srcData.content);
            alert("OCR Complete. Source updated.");
        } catch (e) {
            alert("OCR Failed");
        }
    };

    const handleRemovePage = async (filename: string) => {
        const confirm = window.confirm(`Remove page ${filename} from project?`);
        if (!confirm) return;

        try {
            await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/page`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "remove", filename })
            });
            // Update UI list
            setImages(prev => prev.filter(img => img !== filename));
            // Re-fetch source
            const srcRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/source`);
            const srcData = await srcRes.json();
            setMarkdown(srcData.content);
        } catch (e) {
            alert("Failed to remove page");
        }
    };

    const handleAddPage = async (filename: string) => {
        try {
            await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/page`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "add", filename })
            });
            // Update UI list
            setImages(prev => [...prev, filename].sort());
            // Re-fetch source
            const srcRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/source`);
            const srcData = await srcRes.json();
            setMarkdown(srcData.content);
            setShowAdd(false);
        } catch (e) {
            alert("Failed to add page");
        }
    };

    const handleBatchOCR = async () => {
        const confirm = window.confirm("Process all unprocessed pages? This runs in the background.");
        if (!confirm) return;

        try {
            setIsProcessing(true);
            await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/ocr-all`, {
                method: "POST"
            });
            // Don't wait, polling will handle it
        } catch (e) {
            setIsProcessing(false);
            alert("Failed to start batch job");
        }
    }


    const handleSave = async (checkMode?: 'source' | 'draft') => {
        const modeToSave = checkMode || viewMode;
        try {
            const endpoint = modeToSave === 'source' ? 'source' : 'draft';
            await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/${endpoint}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ content: markdown })
            });
            setOriginalMarkdown(markdown); // Reset dirty flag
            return true;
        } catch (e) {
            alert("Failed to save");
            return false;
        }
    };

    const handleViewSwitch = async (newMode: 'source' | 'draft') => {
        if (newMode === viewMode) return;

        // Auto-Save if dirty
        if (markdown !== originalMarkdown) {
            const saved = await handleSave();
            if (!saved) return; // Stop switch if save failed
        }

        setViewMode(newMode);
        // Effects will trigger re-fetch based on viewMode
    };

    const handleGenerate = async () => {
        // 1. Ensure Source is saved
        if (viewMode === 'source' && markdown !== originalMarkdown) {
            await handleSave('source');
        }

        if (!confirm("Generate a new draft? This will overwrite any existing draft.")) return;

        setIsGenerating(true);
        try {
            await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/generate-draft`, { method: "POST" });
            alert("Draft generation started! Switching to Draft view...");
            setViewMode('draft');
        } catch (e) {
            alert("Failed to start generation");
        }
        setIsGenerating(false);
    };

    const handleCheckIn = async () => {
        if (!confirm("Commit current state to local git?")) return;
        try {
            // Ensure current view is saved first
            if (markdown !== originalMarkdown) {
                await handleSave();
            }

            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/check-in`, { method: "POST" });
            const data = await res.json();
            if (res.ok) {
                alert(data.message);
            } else {
                alert("Check-in failed: " + (data.detail || "Unknown error"));
            }
        } catch (e) {
            alert("Check-in failed");
        }
    };

    const handleExportDoc = async () => {
        if (!confirm("Export current draft to Google Doc?")) return;
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/export-doc`, { method: "POST" });
            const data = await res.json();
            if (res.ok) {
                if (data.url) {
                    window.open(data.url, "_blank");
                } else {
                    alert("Export successful but no URL returned.");
                }
            } else {
                alert("Export failed: " + (data.detail || "Unknown error"));
            }
        } catch (e) {
            alert("Export failed");
        }
    };

    const handleRefine = async (instruction: string) => {
        if (!editorInstance || !selectionRange) return;

        setIsRefining(true);
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/refine-draft`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    selection: selectedText,
                    instruction: instruction
                })
            });
            const data = await res.json();

            if (res.ok && data.refined_text) {
                // Replace text in editor
                const cm = editorInstance.codemirror;
                cm.replaceRange(data.refined_text, selectionRange.from, selectionRange.to);
                // Clear selection state? Or keep it to show result? 
                // Let's keep selection but update it to the new range? 
                // Actually replacing range usually updates cursor. 
                // We'll trust the cursorActivity handler to update state if selection changes/clears.
                alert("Refinement complete!");
            } else {
                alert("Refinement failed: " + (data.detail || "Unknown error"));
            }
        } catch (e) {
            alert("Refinement failed");
        }
        setIsRefining(false);
    };


    if (loading) {
        return <div className="p-10 text-center">Loading / Processing Project...</div>;
    }

    return (
        <div className="flex flex-col h-full">
            {/* Header / Tabs */}
            {/* Header / Tabs */}
            <div className="border-b p-4 bg-white flex justify-between items-center">
                <div className="flex items-center space-x-4">
                    <Link href="/admin/notes-to-sermon/projects" className="text-gray-500 hover:text-gray-700 text-sm">
                        &larr; Back to Projects
                    </Link>
                    <h1 className="text-xl font-bold">Project: {projectTitle}</h1>
                </div>
                <div className="flex items-center space-x-2">
                    <button
                        onClick={handleGenerate}
                        disabled={isGenerating}
                        className={`bg-indigo-600 text-white px-3 py-1 rounded text-sm hover:bg-indigo-700 ${isGenerating ? 'opacity-50' : ''}`}
                    >
                        {isGenerating ? "Generating..." : "Generate Draft"}
                    </button>
                    <button
                        onClick={() => handleSave()}
                        className={`px-4 py-1 rounded font-bold text-white ${markdown !== originalMarkdown ? 'bg-red-500 hover:bg-red-600' : 'bg-green-600 hover:bg-green-700'}`}
                    >
                        {markdown !== originalMarkdown ? 'Save*' : 'Saved'}
                    </button>
                    <button
                        onClick={handleCheckIn}
                        className="px-3 py-1 rounded font-bold text-gray-700 bg-gray-200 hover:bg-gray-300 text-sm"
                        title="Commit to local git"
                    >
                        Check In
                    </button>
                    <button
                        onClick={handleExportDoc}
                        className="px-3 py-1 rounded font-bold text-blue-700 bg-blue-100 hover:bg-blue-200 text-sm"
                        title="Export to Google Doc"
                    >
                        Export to Doc
                    </button>
                </div>
            </div>

            <div className="flex h-full flex-1 overflow-hidden">
                {/* Left Pane: Multi-Page Image Viewer */}
                {viewMode === 'source' && (
                    <div className="w-1/2 bg-gray-100 border-r overflow-auto p-4">
                        <div className="mb-4 flex justify-between items-center">
                            <div className="flex items-center space-x-2">
                                <h3 className="font-bold text-gray-700">Pages ({images.length})</h3>
                                <button
                                    onClick={() => setShowAdd(true)}
                                    className="text-gray-500 hover:text-indigo-600 font-bold text-lg bg-gray-200 w-6 h-6 flex items-center justify-center rounded-full"
                                    title="Add Page"
                                >
                                    +
                                </button>
                            </div>
                            <button
                                onClick={handleBatchOCR}
                                disabled={isProcessing}
                                className={`text-white text-xs px-3 py-1 rounded ${isProcessing ? 'bg-gray-400' : 'bg-indigo-600 hover:bg-indigo-700'}`}
                            >
                                {isProcessing ? "Processing..." : "Process All"}
                            </button>
                        </div>
                        {isProcessing && (
                            <div className="bg-yellow-100 border-l-4 border-yellow-500 text-yellow-700 p-2 mb-2 text-sm">
                                Processing pages in background... text will update automatically.
                            </div>
                        )}
                        <div className="space-y-4 max-w-[800px] mx-auto">
                            {images.map(img => (
                                <div
                                    key={img}
                                    id={`page-${img}`}
                                    className="bg-white shadow border p-2 relative group cursor-pointer ring-0 hover:ring-2 hover:ring-indigo-300 transition-all"
                                    onClick={(e) => {
                                        // Prevent triggering when clicking inner buttons
                                        if ((e.target as HTMLElement).tagName === "BUTTON") return;
                                        scrollToMarkdown(img);
                                    }}
                                >
                                    <div className="absolute top-2 left-2 right-2 flex justify-between z-10">
                                        <span className="bg-black/50 text-white px-2 py-1 text-xs rounded truncate max-w-[150px]">{img}</span>
                                        <div className="space-x-1 hidden group-hover:flex">
                                            <button
                                                onClick={() => handleOCR(img)}
                                                className="bg-indigo-600 text-white text-xs px-2 py-1 rounded hover:bg-indigo-700 shadow-sm"
                                                title="Run OCR"
                                            >
                                                OCR
                                            </button>
                                            <button
                                                onClick={() => handleRemovePage(img)}
                                                className="bg-red-600 text-white text-xs px-2 py-1 rounded hover:bg-red-700 shadow-sm"
                                                title="Remove Page"
                                            >
                                                &times;
                                            </button>
                                        </div>
                                    </div>
                                    {/* Use existing image endpoint */}
                                    <img
                                        src={`/api/admin/notes-to-sermon/image/${img}`}
                                        alt={img}
                                        className="w-full h-auto"
                                    />
                                </div>
                            ))}
                            {images.length === 0 && <p className="text-center p-10">No images attached.</p>}
                        </div>

                        {/* Add Page Section */}
                        <div className="mt-8 border-t pt-4">
                            {!showAdd ? (
                                <button
                                    onClick={() => setShowAdd(true)}
                                    className="w-full py-2 border-2 border-dashed border-gray-300 text-gray-500 hover:border-indigo-500 hover:text-indigo-600 rounded"
                                >
                                    + Add Page
                                </button>
                            ) : (
                                <div className="bg-white p-2 border rounded">
                                    <div className="flex justify-between items-center mb-2">
                                        <span className="font-bold text-sm">Select Page to Add</span>
                                        <button onClick={() => setShowAdd(false)} className="text-gray-400">&times;</button>
                                    </div>
                                    <div className="max-h-[200px] overflow-auto">
                                        {allImages
                                            .filter(img => !images.includes(img))
                                            .map(img => (
                                                <div
                                                    key={img}
                                                    className="p-2 hover:bg-gray-100 cursor-pointer text-sm flex justify-between"
                                                    onClick={() => handleAddPage(img)}
                                                >
                                                    {img} <span>+</span>
                                                </div>
                                            ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                )}

                {/* Right Pane: Unified Editor + AI Panel */}
                <div className={`${viewMode === 'source' ? 'w-1/2' : 'w-full'} bg-white flex flex-row border-l`}>
                    <div className="flex-1 flex flex-col overflow-hidden p-4">
                        <div className="mb-2 text-sm text-gray-500 flex justify-between items-center">
                            <div className="flex items-center space-x-4">
                                <div className="flex bg-gray-200 rounded p-1 inline-flex">
                                    <button
                                        className={`px-3 py-1 text-sm rounded ${viewMode === 'source' ? 'bg-white shadow' : 'text-gray-600'}`}
                                        onClick={() => handleViewSwitch('source')}
                                    >
                                        Unified Input
                                    </button>
                                    <button
                                        className={`px-3 py-1 text-sm rounded ${viewMode === 'draft' ? 'bg-white shadow' : 'text-gray-600'}`}
                                        onClick={() => handleViewSwitch('draft')}
                                    >
                                        Generated Draft
                                    </button>
                                </div>
                                <span>{viewMode === 'source' ? 'Unified Manuscript' : 'Sermon Draft'} ({markdown.length} chars)</span>
                            </div>
                        </div>
                        <div className="flex-1 overflow-y-auto">
                            <SimpleMDE
                                value={markdown}
                                onChange={(value) => setMarkdown(value)}
                                options={editorOptions}
                                className="h-full"
                                getMdeInstance={setEditorInstance}
                            />
                        </div>
                    </div>
                    {viewMode === 'draft' && (
                        <div className="border-l h-full">
                            <AiCommandPanel
                                selectedText={selectedText}
                                onRefine={handleRefine}
                                isRefining={isRefining}
                            />
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

"use client";

import React, { useState, useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import "easymde/dist/easymde.min.css";

import AiCommandPanel from "./AiCommandPanel";

const SimpleMDE = dynamic(() => import("react-simplemde-editor"), {
    ssr: false,
});

import TheologicalAuditPanel from "./TheologicalAuditPanel";

export default function MultiPageEditor({ projectId }: { projectId: string }) {
    const router = useRouter();
    const [viewMode, setViewMode] = useState<'source' | 'draft' | 'final'>('source');
    const [hasFinal, setHasFinal] = useState(false);
    const [isGenerating, setIsGenerating] = useState(false);
    const [projectTitle, setProjectTitle] = useState(projectId);
    const [bibleVerse, setBibleVerse] = useState("");
    const [showMetaEdit, setShowMetaEdit] = useState(false);
    const [metaForm, setMetaForm] = useState({ title: "", bibleVerse: "", googleDocLink: "" });
    const [googleDocLink, setGoogleDocLink] = useState("");
    const [prompts, setPrompts] = useState<any[]>([]);
    const [selectedPromptId, setSelectedPromptId] = useState<string>("");
    const [usedPromptId, setUsedPromptId] = useState<string | null>(null);
    const [auditPassed, setAuditPassed] = useState<boolean | null>(null);
    const [projectType, setProjectType] = useState<string>("sermon_note");

    // Content State
    const [markdown, setMarkdown] = useState("");
    const [originalMarkdown, setOriginalMarkdown] = useState(""); // Track for dirty check
    const [images, setImages] = useState<string[]>([]);
    const [allImages, setAllImages] = useState<string[]>([]); // For adding new pages
    const [loading, setLoading] = useState(true);
    const [isProcessing, setIsProcessing] = useState(false);
    const [showAdd, setShowAdd] = useState(false);
    const [editorInstance, setEditorInstance] = useState<any>(null);

    const editorOptions = useMemo(() => ({
        spellChecker: false,
        status: false,
        previewClass: ["editor-preview", "prose", "prose-indigo", "max-w-none", "prose-p:leading-relaxed", "prose-headings:font-bold"],
        placeholder: viewMode === 'source' ? (projectType === 'transcript' ? "Enter raw transcript here..." : "Unified manuscript will appear here...") : (viewMode === 'draft' ? "Generated Draft will appear here..." : "Master Text will appear here..."),
        minHeight: "500px",
    }), [viewMode, projectType]);



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
                const endpoint = viewMode;
                const srcRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/${endpoint}`, {
                    headers: {
                        'Cache-Control': 'no-cache',
                        'Pragma': 'no-cache'
                    }
                });
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
                if (metaData.bible_verse) {
                    setBibleVerse(metaData.bible_verse);
                }
                if (metaData.google_doc_id) {
                    setGoogleDocLink(`https://docs.google.com/document/d/${metaData.google_doc_id}/edit`);
                }
                if (metaData.processing) {
                    setIsProcessing(true);
                }
                if (metaData.prompt_id) {
                    setUsedPromptId(metaData.prompt_id);
                }
                if (metaData.project_type) {
                    setProjectType(metaData.project_type);
                }
                if (metaData.audit_passed !== undefined) {
                    setAuditPassed(metaData.audit_passed);
                }

                const finalRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/final`);
                if (finalRes.ok) {
                    const finalData = await finalRes.json();
                    if (finalData.content) {
                        setHasFinal(true);
                    }
                }

                setLoading(false);
            } catch (e) {
                alert("Error loading project");
            }
        };
        load();
    }, [projectId, viewMode]);

    // Load Prompts
    useEffect(() => {
        fetch("/api/admin/notes-to-sermon/prompts")
            .then(res => res.json())
            .then(data => {
                setPrompts(data);
                if (data.length > 0) {
                    // Try to finding default
                    const def = data.find((p: any) => p.is_default);
                    if (def) setSelectedPromptId(def.id);
                    else setSelectedPromptId(data[0].id);
                }
            });
    }, []);

    interface ProgressState {
        current?: number;
        total?: number;
        stage?: string;
        progress?: number;
    }

    const [progress, setProgress] = useState<ProgressState | null>(null);

    // Polling for status
    useEffect(() => {
        let interval: NodeJS.Timeout;
        if (isProcessing) {
            interval = setInterval(async () => {
                const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}`);
                const data = await res.json();

                // Update progress if available
                if (data.progress) {
                    setProgress(data.progress);
                }

                // Update prompt ID if available (it might be set during processing)
                if (data.prompt_id) {
                    setUsedPromptId(data.prompt_id);
                }


                if (!data.processing) {
                    setIsProcessing(false);
                    setProgress(null);
                    // Re-fetch content based on current viewMode
                    const endpoint = viewMode;
                    const srcRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/${endpoint}`, {
                        headers: {
                            'Cache-Control': 'no-cache',
                            'Pragma': 'no-cache'
                        }
                    });
                    const srcData = await srcRes.json();

                    const content = srcData.content || "";
                    setMarkdown(content);
                    setOriginalMarkdown(content);

                    alert("Processing Complete!");
                }
            }, 3000);
        }
        return () => clearInterval(interval);
    }, [isProcessing, projectId, viewMode]);



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
            const srcRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/source`, {
                headers: {
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                }
            });
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
            const srcRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/source`, {
                headers: {
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                }
            });
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
            const srcRes = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/source`, {
                headers: {
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                }
            });
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
            const endpoint = modeToSave;
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

    const handleViewSwitch = async (newMode: 'source' | 'draft' | 'final') => {
        if (newMode === viewMode) return;

        // Auto-Save if dirty
        if (markdown !== originalMarkdown && !isProcessing) {
            const saved = await handleSave();
            if (!saved) return; // Stop switch if save failed
        }

        setViewMode(newMode);
        setIsProcessing(true);
        // Effects will trigger re-fetch based on viewMode
    };

    // Auto-toggle Preview mode for Master Text
    useEffect(() => {
        if (!editorInstance) return;

        // Wait for next tick to ensure DOM is ready for togglePreview
        const timer = setTimeout(() => {
            const isPreviewActive = editorInstance.isPreviewActive();

            if (viewMode === 'final' && !isPreviewActive) {
                editorInstance.togglePreview();
            } else if (viewMode !== 'final' && isPreviewActive) {
                editorInstance.togglePreview();
            }
        }, 50);

        return () => clearTimeout(timer);
    }, [viewMode, editorInstance]);

    const handleGenerate = async () => {
        // 1. Ensure Source is saved
        if (viewMode === 'source' && markdown !== originalMarkdown) {
            await handleSave('source');
        }

        if (!confirm("Generate a new draft? This will overwrite any existing draft.")) return;

        setIsGenerating(true);
        try {
            await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/generate-draft`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ prompt_id: selectedPromptId })
            });
            // alert("Draft generation started! Switching to Draft view...");
            // setViewMode('draft');
            router.push(`/admin/notes-to-sermon/project/${projectId}/generation`);
        } catch (e) {
            alert("Failed to start generation");
        }
        setIsGenerating(false);
    };

    const handleStartTheologicalReview = async () => {
        if (!confirm("Start Theological Review? This will create a final text copy of the current draft.")) return;
        try {
            if (markdown !== originalMarkdown) {
                await handleSave();
            }
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/start-review`, { method: "POST" });
            if (res.ok) {
                setHasFinal(true);
                setViewMode('final');
            } else {
                alert("Failed to start review.");
            }
        } catch (e) {
            alert("Error starting review");
        }
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
                    setGoogleDocLink(data.url);
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



    const handleOpenMetaEdit = () => {
        setMetaForm({ title: projectTitle, bibleVerse: bibleVerse, googleDocLink: googleDocLink });
        setShowMetaEdit(true);
    };

    const handleSaveMeta = async () => {
        if (!metaForm.title) return alert("Title is required");

        try {
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/metadata`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    title: metaForm.title,
                    bible_verse: metaForm.bibleVerse,
                    google_doc_link: metaForm.googleDocLink
                })
            });
            if (res.ok) {
                setProjectTitle(metaForm.title);
                setBibleVerse(metaForm.bibleVerse);
                setGoogleDocLink(metaForm.googleDocLink);
                setShowMetaEdit(false);
            } else {
                const data = await res.json();
                alert("Failed to update metadata: " + (data.detail || "Unknown error"));
            }
        } catch (e) {
            alert("Error updating metadata");
        }
    };



    // Callback from AiCommandPanel to reload project meta so the lock is lifted
    const handleAuditComplete = async () => {
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}`);
            if (res.ok) {
                const data = await res.json();
                if (data.audit_passed !== undefined) {
                    setAuditPassed(data.audit_passed);
                }
            }
        } catch (e) {
            console.error("Failed to reload meta after audit", e);
        }
    };

    // Callback from AiCommandPanel to highlight text in the editor
    const handleHighlightText = (evidence: string) => {
        if (!editorInstance) return;
        const cm = editorInstance.codemirror;

        let lines = cm.getValue().split('\n');
        let textToFind = evidence.trim();
        if (!textToFind) return;

        // Helper function to search for a specific string in the editor lines
        const findAndSelect = (searchStr: string, exact: boolean = true) => {
            if (!searchStr) return false;

            // Handle ellipses: if the LLM used "..." or "…" or "⋯", split by it and find the longest contiguous chunk
            const ellipsisRegex = /(\.{2,}|…+|⋯+|etc)/i;
            const parts = searchStr.split(ellipsisRegex)
                .filter(p => !ellipsisRegex.test(p))
                .map(p => p.trim())
                .filter(p => p.length > 0);

            let target = searchStr;
            if (parts.length > 0) {
                // Sort parts by length and take the longest one to maximize uniqueness
                target = parts.sort((a, b) => b.length - a.length)[0];
            }

            for (let i = 0; i < lines.length; i++) {
                let lineText = lines[i];
                let idx = lineText.indexOf(target);
                if (idx !== -1) {
                    let startPos = { line: i, ch: idx };
                    let endPos = { line: i, ch: exact ? idx + target.length : lineText.length };
                    cm.scrollIntoView(startPos, 150);
                    cm.setSelection(startPos, endPos);
                    cm.focus();
                    return true;
                }
            }
            return false;
        };

        // 1. Try exact full evidence string matching
        if (findAndSelect(textToFind, true)) return;

        // 2. Try extracting text inside Traditional Chinese or English quotes 
        // e.g., 引用12:5並連到民28:9-10；說「祭司在殿裡可以超過安息日的律法，殿比安息日更大。」
        const quoteRegexes = [
            /「([^」]+)」/,  // Traditional Chinese single quotes
            /『([^』]+)』/,  // Traditional Chinese double quotes
            /"([^"]+)"/,    // English double quotes
            /'([^']+)'/     // English single quotes
        ];

        for (const regex of quoteRegexes) {
            const match = textToFind.match(regex);
            if (match && match[1]) {
                const extractedText = match[1].trim();
                // Try to find the exact extracted text
                if (findAndSelect(extractedText, true)) return;

                // If the extracted text is long, maybe try a partial match on the extracted text
                if (extractedText.length > 10) {
                    const partialExtracted = extractedText.substring(0, 10);
                    if (findAndSelect(partialExtracted, false)) return;
                }
            }
        }

        // 3. Try partial match of the original string (fallback)
        let partialText = textToFind.substring(0, Math.min(10, textToFind.length)).trim();
        if (partialText.length > 3 && findAndSelect(partialText, false)) {
            return;
        }

        alert("找不到匹配的經文或字串，可能已被手動修改。");
    };

    if (loading) {
        return <div className="p-10 text-center">Loading / Processing Project...</div>;
    }

    return (
        <div className="flex flex-col h-full">
            {/* Header / Tabs */}
            <div className="border-b p-4 bg-white flex justify-between items-center">
                <div className="flex items-center space-x-4">
                    <Link href="/admin/notes-to-sermon/projects" className="text-gray-500 hover:text-gray-700 text-sm">
                        &larr; Back to Projects
                    </Link>
                    <div>
                        <div className="flex items-center space-x-2">
                            <h1 className="text-xl font-bold">Project: {projectTitle}</h1>
                            <button onClick={handleOpenMetaEdit} className="text-gray-400 hover:text-indigo-600">
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                                </svg>
                            </button>
                        </div>
                        <div className="flex items-center space-x-2">
                            {bibleVerse && <p className="text-sm text-gray-600">{bibleVerse}</p>}
                            {googleDocLink && (
                                <a href={googleDocLink} target="_blank" rel="noopener noreferrer" className="text-sm text-blue-600 hover:text-blue-800 underline">
                                    Google Doc
                                </a>
                            )}
                        </div>
                    </div>
                </div>
                <div className="flex items-center space-x-2">
                    {/* Prompt Selector Removed */}

                    <button
                        onClick={handleGenerate}
                        disabled={isGenerating}
                        className={`bg-indigo-600 text-white px-3 py-1 rounded text-sm hover:bg-indigo-700 ${isGenerating ? 'opacity-50' : ''}`}
                    >
                        {isGenerating ? "Generating..." : "Generate Draft"}
                    </button>
                    {!hasFinal && auditPassed === true && (
                        <button
                            onClick={handleStartTheologicalReview}
                            className={`px-3 py-1 rounded font-bold text-sm text-white bg-green-600 hover:bg-green-700`}
                            title="Start Theological Review"
                        >
                            Start Theological Review
                        </button>
                    )}
                    <button
                        onClick={() => handleSave()}
                        className={`px-4 py-1 rounded font-bold text-white ${markdown !== originalMarkdown ? 'bg-red-500 hover:bg-red-600' : 'bg-green-600 hover:bg-green-700'}`}
                    >
                        {markdown !== originalMarkdown ? 'Save*' : 'Saved'}
                    </button>
                    <button
                        onClick={handleCheckIn}
                        disabled={auditPassed !== true}
                        className={`px-3 py-1 rounded font-bold text-sm ${auditPassed === true ? 'text-gray-700 bg-gray-200 hover:bg-gray-300' : 'text-gray-400 bg-gray-100 cursor-not-allowed'}`}
                        title={auditPassed === true ? "Commit to local git" : "Must pass AI Audit first"}
                    >
                        Check In
                    </button>
                    <button
                        onClick={handleExportDoc}
                        disabled={auditPassed !== true}
                        className={`px-3 py-1 rounded font-bold text-sm ${auditPassed === true ? 'text-blue-700 bg-blue-100 hover:bg-blue-200' : 'text-gray-400 bg-gray-100 cursor-not-allowed'}`}
                        title={auditPassed === true ? "Export to Google Doc" : "Must pass AI Audit first"}
                    >
                        Export to Doc
                    </button>
                </div>
            </div>

            <div className="flex h-full flex-1 overflow-hidden">
                {/* Left Pane: Multi-Page Image Viewer */}
                {viewMode === 'source' && projectType !== 'transcript' && (
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
                        {isProcessing && progress && (
                            <div className="bg-white border rounded p-3 mb-4 shadow-sm space-y-2">
                                <div className="flex justify-between text-sm font-bold text-gray-700">
                                    <span>{progress.stage ? progress.stage : "Processing..."}</span>
                                    {progress.total && progress.current !== undefined ? (
                                        <span>{progress.current} / {progress.total}</span>
                                    ) : (
                                        <span>{progress.progress || 0}%</span>
                                    )}
                                </div>
                                <div className="w-full bg-gray-200 rounded-full h-2.5">
                                    <div
                                        className="bg-indigo-600 h-2.5 rounded-full transition-all duration-500 ease-in-out"
                                        style={{
                                            width: (progress.total && progress.current !== undefined)
                                                ? `${(progress.current / progress.total) * 100}%`
                                                : `${progress.progress || 5}%`
                                        }}
                                    ></div>
                                </div>
                                <p className="text-xs text-gray-500 italic text-center">Do not close this window.</p>
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
                                    {/* eslint-disable-next-line @next/next/no-img-element */}
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
                <div className={`${viewMode === 'source' && projectType !== 'transcript' ? 'w-1/2' : 'w-full'} bg-white flex flex-row border-l`}>
                    <div className="flex-1 flex flex-col overflow-hidden p-4">
                        <div className="mb-2 text-sm text-gray-500 flex justify-between items-center">
                            <div className="flex items-center space-x-4 w-full justify-between">
                                <div className="flex items-center space-x-4">
                                    <div className="flex bg-gray-200 rounded p-1 inline-flex items-center">
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
                                        {hasFinal && (
                                            <button
                                                className={`px-3 py-1 text-sm rounded ${viewMode === 'final' ? 'bg-white shadow' : 'text-gray-600'}`}
                                                onClick={() => handleViewSwitch('final')}
                                            >
                                                Master Text (Final)
                                            </button>
                                        )}
                                    </div>
                                    <span>{viewMode === 'source' ? 'Unified Manuscript' : viewMode === 'draft' ? 'Sermon Draft' : 'Master Text Final'} ({markdown.length} chars)</span>
                                </div>
                            </div>
                        </div>
                        <div className="flex-1 overflow-y-auto prose-editor-container">
                            <SimpleMDE
                                value={markdown}
                                onChange={(value) => setMarkdown(value)}
                                options={editorOptions}
                                className="h-full"
                                getMdeInstance={setEditorInstance}
                            />
                        </div>
                    </div>
                    <div className={`border-l h-full ${viewMode === 'draft' || viewMode === 'final' ? 'block' : 'hidden'}`}>
                        {viewMode === 'draft' ? (
                            <AiCommandPanel
                                projectId={projectId}
                                onAuditComplete={handleAuditComplete}
                                onHighlightText={handleHighlightText}
                            />
                        ) : viewMode === 'final' ? (
                            <TheologicalAuditPanel
                                projectId={projectId}
                                onHighlightText={handleHighlightText}
                            />
                        ) : null}
                    </div>
                </div>
            </div>
            {/* Metadata Modal */}
            {showMetaEdit && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white p-6 rounded shadow-lg w-[400px]">
                        <h3 className="text-lg font-bold mb-4">Edit Project Info</h3>
                        <div className="space-y-4">
                            <div>
                                <label className="block text-sm font-bold mb-1">Title</label>
                                <input
                                    type="text"
                                    className="w-full border p-2 rounded"
                                    value={metaForm.title}
                                    onChange={e => setMetaForm({ ...metaForm, title: e.target.value })}
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-bold mb-1">Bible Verse</label>
                                <textarea
                                    className="w-full border p-2 rounded h-24"
                                    placeholder="e.g. John 3:16"
                                    value={metaForm.bibleVerse}
                                    onChange={e => setMetaForm({ ...metaForm, bibleVerse: e.target.value })}
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-bold mb-1">Google Doc Link</label>
                                <input
                                    type="text"
                                    className="w-full border p-2 rounded"
                                    placeholder="Optional: https://docs.google.com/document/d/..."
                                    value={metaForm.googleDocLink}
                                    onChange={e => setMetaForm({ ...metaForm, googleDocLink: e.target.value })}
                                />
                            </div>
                            <div className="flex justify-end space-x-2">
                                <button
                                    onClick={() => setShowMetaEdit(false)}
                                    className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={handleSaveMeta}
                                    className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700"
                                >
                                    Save
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

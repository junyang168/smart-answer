"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

// Types
interface Project {
    id: string;
    title: string;
    series_id?: string;
    lecture_id?: string;
}

interface Lecture {
    id: string;
    title: string;
    description?: string;
    folder?: string;
    project_ids: string[];
}

interface LectureSeries {
    id: string;
    title: string;
    description?: string;
    folder?: string;
    lectures: Lecture[];
}

export default function SeriesDetailPage() {
    const params = useParams();
    const seriesId = params.seriesId as string;
    const router = useRouter();

    const [series, setSeries] = useState<LectureSeries | null>(null);
    const [allProjects, setAllProjects] = useState<Project[]>([]);
    const [isLoading, setIsLoading] = useState(true);

    // Lecture UI State
    const [isCreatingLecture, setIsCreatingLecture] = useState(false);
    const [editingLecture, setEditingLecture] = useState<Lecture | null>(null);
    const [lectureTitle, setLectureTitle] = useState("");
    const [lectureDesc, setLectureDesc] = useState("");
    const [lectureFolder, setLectureFolder] = useState("");
    const [availableLectureFolders, setAvailableLectureFolders] = useState<string[]>([]);

    // ... (rest of states)

    // ... (fetchData etc)

    const handleOpenCreateLecture = () => {
        setEditingLecture(null);
        setLectureTitle("");
        setLectureDesc("");
        setLectureFolder("");
        setIsCreatingLecture(true);
    };

    const handleOpenEditLecture = (lecture: Lecture) => {
        setEditingLecture(lecture);
        setLectureTitle(lecture.title);
        setLectureDesc(lecture.description || "");
        setLectureFolder(lecture.folder || "");
        setIsCreatingLecture(true);
    };

    const handleSaveLecture = async () => {
        if (!lectureTitle) return alert("Title required");

        const payload = {
            title: lectureTitle,
            description: lectureDesc,
            folder: lectureFolder || undefined
        };

        try {
            let res;
            if (editingLecture) {
                // Update
                res = await fetch(`/api/admin/notes-to-sermon/series/${seriesId}/lectures/${editingLecture.id}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
            } else {
                // Create
                res = await fetch(`/api/admin/notes-to-sermon/series/${seriesId}/lectures`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
            }

            if (res.ok) {
                // Re-fetch to be safe or optimistic update
                fetchData();
                setIsCreatingLecture(false);
                setEditingLecture(null);
                setLectureTitle("");
                setLectureDesc("");
                setLectureFolder("");
            } else {
                alert("Failed to save lecture");
            }
        } catch (e) {
            alert("Error saving lecture");
        }
    };

    // Assign Project UI State
    const [assignTargetLectureId, setAssignTargetLectureId] = useState<string | null>(null);
    const [selectedProjectId, setSelectedProjectId] = useState("");

    // Project Creation UI State
    const [creatingProjectForLecture, setCreatingProjectForLecture] = useState<Lecture | null>(null);
    const [projectImages, setProjectImages] = useState<any[]>([]);
    const [selectedImages, setSelectedImages] = useState<string[]>([]);
    const [newProjectTitle, setNewProjectTitle] = useState("");
    const [isFetchingImages, setIsFetchingImages] = useState(false);

    const handleOpenCreateProject = async (lecture: Lecture) => {
        if (!series?.folder || !lecture.folder) {
            alert("Cannot create project: Series or Lecture folder is missing.");
            return;
        }
        setCreatingProjectForLecture(lecture);
        setNewProjectTitle(lecture.title); // Default title
        setIsFetchingImages(true);
        setSelectedImages([]);

        try {
            // Construct the relative folder path: series_folder/lecture_folder
            const folderPath = `${series.folder}/${lecture.folder}`;
            // Fetch images from this folder
            const res = await fetch(`/api/admin/notes-to-sermon/images?folder=${encodeURIComponent(folderPath)}`);
            if (res.ok) {
                const imgs = await res.json();
                setProjectImages(imgs);
            } else {
                alert("Failed to fetch images from folder");
            }
        } catch (e) {
            alert("Error fetching images");
        } finally {
            setIsFetchingImages(false);
        }
    };

    const handleCreateProject = async () => {
        if (!creatingProjectForLecture) return;
        if (!newProjectTitle) return alert("Title required");
        if (selectedImages.length === 0) return alert("Select at least one image");

        try {
            const res = await fetch("/api/admin/notes-to-sermon/sermon-project", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    title: newProjectTitle,
                    pages: selectedImages,
                    series_id: seriesId,
                    lecture_id: creatingProjectForLecture.id
                })
            });

            if (res.ok) {
                alert("Project created and linked!");
                setCreatingProjectForLecture(null);
                fetchData(); // Refresh to show new project linked
            } else {
                alert("Failed to create project");
            }
        } catch (e) {
            alert("Error creating project");
        }
    };

    const toggleImageSelection = (filename: string) => {
        setSelectedImages(prev =>
            prev.includes(filename) ? prev.filter(f => f !== filename) : [...prev, filename]
        );
    };

    useEffect(() => {
        if (seriesId) {
            fetchData();
        }
    }, [seriesId]);

    const fetchData = async () => {
        setIsLoading(true);
        try {
            const [serRes, projRes] = await Promise.all([
                fetch(`/api/admin/notes-to-sermon/series/${seriesId}`),
                fetch(`/api/admin/notes-to-sermon/sermon-projects`) // Corrected endpoint URL
            ]);

            if (serRes.ok) {
                const sData = await serRes.json();
                setSeries(sData);
                // If series has a folder, fetch its subfolders for lecture selection
                if (sData.folder) {
                    fetchLectureFolders(sData.folder);
                }
            }
            if (projRes.ok) {
                setAllProjects(await projRes.json());
            }
        } catch (e) {
            console.error("Error fetching data", e);
        } finally {
            setIsLoading(false);
        }
    };

    const fetchLectureFolders = async (seriesFolder: string) => {
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/series/folders/${seriesFolder}`);
            if (res.ok) {
                setAvailableLectureFolders(await res.json());
            }
        } catch (e) {
            console.error("Failed to fetch lecture folders", e);
        }
    };

    const handleCreateLecture = async () => {
        if (!lectureTitle) return alert("Title required");
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/series/${seriesId}/lectures`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    title: lectureTitle,
                    description: lectureDesc,
                    folder: lectureFolder || undefined
                }),
            });
            if (res.ok) {
                const newLecture = await res.json();
                setSeries(prev => prev ? {
                    ...prev,
                    lectures: [...prev.lectures, newLecture]
                } : null);
                setIsCreatingLecture(false);
                setLectureTitle("");
                setLectureDesc("");
                setLectureFolder("");
            } else {
                alert("Failed to create lecture");
            }
        } catch (e) {
            alert("Error creating lecture");
        }
    };

    const handleDeleteLecture = async (lectureId: string) => {
        if (!confirm("Delete this lecture?")) return;
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/series/${seriesId}/lectures/${lectureId}`, {
                method: "DELETE"
            });
            if (res.ok) {
                setSeries(prev => prev ? {
                    ...prev,
                    lectures: prev.lectures.filter(l => l.id !== lectureId)
                } : null);
            }
        } catch (e) {
            alert("Error deleting lecture");
        }
    };

    const handleAssignProject = async () => {
        if (!assignTargetLectureId || !selectedProjectId) return;
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/series/${seriesId}/lectures/${assignTargetLectureId}/projects`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project_id: selectedProjectId })
            });

            if (res.ok) {
                // Optimistically update OR re-fetch. Re-fetching is safer for sync.
                fetchData();
                setAssignTargetLectureId(null);
                setSelectedProjectId("");
            } else {
                alert("Failed to assign project");
            }
        } catch (e) {
            alert("Error assigning project");
        }
    };

    const handleRemoveProject = async (lectureId: string, projectId: string) => {
        if (!confirm("Remove project from lecture?")) return;
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/series/${seriesId}/lectures/${lectureId}/projects/${projectId}`, {
                method: "DELETE"
            });
            if (res.ok) {
                fetchData();
            }
        } catch (e) {
            alert("Error removing project");
        }
    };

    const handleReorder = async (lectureId: string, newOrder: string[]) => {
        try {
            // Optimistic update
            setSeries(prev => {
                if (!prev) return null;
                return {
                    ...prev,
                    lectures: prev.lectures.map(l => l.id === lectureId ? { ...l, project_ids: newOrder } : l)
                }
            });

            const res = await fetch(`/api/admin/notes-to-sermon/series/${seriesId}/lectures/${lectureId}/projects/reorder`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project_ids: newOrder })
            });

            if (!res.ok) {
                alert("Failed to save order");
                fetchData(); // Revert
            }
        } catch (e) {
            alert("Error saving order");
            fetchData();
        }
    };

    const moveProject = (lectureId: string, currentOrder: string[], index: number, direction: 'up' | 'down') => {
        if (direction === 'up' && index === 0) return;
        if (direction === 'down' && index === currentOrder.length - 1) return;

        const newOrder = [...currentOrder];
        const swapIndex = direction === 'up' ? index - 1 : index + 1;
        [newOrder[index], newOrder[swapIndex]] = [newOrder[swapIndex], newOrder[index]];

        handleReorder(lectureId, newOrder);
    };

    if (isLoading) return <div className="p-10 text-center">Loading...</div>;
    if (!series) return <div className="p-10 text-center">Series not found</div>;

    return (
        <div className="container mx-auto p-6">
            <div className="mb-6">
                <Link href="/admin/notes-to-sermon/series" className="text-gray-500 hover:text-gray-700 text-sm">
                    &larr; Back to Series List
                </Link>
                <div className="flex justify-between items-start mt-2">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900">{series.title}</h1>
                        <p className="text-gray-600 mt-1">{series.description}</p>
                        {series.folder && (
                            <p className="text-xs text-gray-400 mt-1">Source Folder: {series.folder}</p>
                        )}
                    </div>
                    {/* Series Actions (Edit/Delete) could go here */}
                </div>
            </div>

            <hr className="my-6" />

            {/* Lectures Section */}
            <div className="flex justify-between items-center mb-4">
                <h2 className="text-xl font-bold">Lectures</h2>
                <button
                    onClick={handleOpenCreateLecture}
                    className="bg-blue-600 text-white px-3 py-1.5 rounded hover:bg-blue-700 text-sm"
                >
                    + Add Lecture
                </button>
            </div>

            {isCreatingLecture && (
                <div className="bg-gray-50 p-4 rounded border mb-6">
                    <h3 className="font-semibold mb-2">{editingLecture ? "Edit Lecture" : "New Lecture"}</h3>
                    <input
                        className="border p-2 rounded w-full mb-2"
                        placeholder="Lecture Title"
                        value={lectureTitle}
                        onChange={e => setLectureTitle(e.target.value)}
                    />
                    <input
                        className="border p-2 rounded w-full mb-2"
                        placeholder="Description (optional)"
                        value={lectureDesc}
                        onChange={e => setLectureDesc(e.target.value)}
                    />

                    {series.folder ? (
                        <div className="mb-2">
                            <select
                                className="border p-2 rounded w-full bg-white"
                                value={lectureFolder}
                                onChange={e => setLectureFolder(e.target.value)}
                            >
                                <option value="">Select Folder (Optional)...</option>
                                {availableLectureFolders.map(f => (
                                    <option key={f} value={f}>{f}</option>
                                ))}
                            </select>
                            <p className="text-[10px] text-gray-500 mt-0.5">
                                Select subfolder from .../images/{series.folder}/
                            </p>
                        </div>
                    ) : (
                        <div className="mb-2 text-xs text-amber-600">
                            Note: Series has no source folder selected, so no subfolders available.
                        </div>
                    )}

                    <div className="flex justify-end space-x-2">
                        <button onClick={() => setIsCreatingLecture(false)} className="px-3 py-1 text-gray-600">Cancel</button>
                        <button onClick={handleSaveLecture} className="px-3 py-1 bg-blue-600 text-white rounded">
                            {editingLecture ? "Update" : "Create"}
                        </button>
                    </div>
                </div>
            )}

            <div className="space-y-6">
                {series.lectures.length === 0 && (
                    <div className="text-gray-500 italic">No lectures yet.</div>
                )}
                {series.lectures.map((lecture, index) => (
                    <div key={lecture.id} className="border rounded-lg p-5 bg-white shadow-sm">
                        <div className="flex justify-between items-start mb-3">
                            <div>
                                <h3 className="text-lg font-bold text-gray-800">
                                    <span className="text-gray-400 font-normal mr-2">#{index + 1}</span>
                                    {lecture.title}
                                </h3>
                                {(lecture.description || lecture.folder) && (
                                    <p className="text-sm text-gray-500">
                                        {lecture.description}
                                        {lecture.folder && <span className="ml-2 px-1 bg-gray-100 rounded text-xs font-mono">/{lecture.folder}</span>}
                                    </p>
                                )}
                            </div>
                            <div className="flex space-x-2">
                                <button
                                    onClick={() => handleOpenEditLecture(lecture)}
                                    className="text-blue-600 hover:text-blue-800 text-sm"
                                >
                                    Edit
                                </button>
                                <button
                                    onClick={() => handleDeleteLecture(lecture.id)}
                                    className="text-red-400 hover:text-red-600 text-sm"
                                >
                                    Delete
                                </button>
                            </div>
                        </div>

                        {/* Projects in Lecture */}
                        <div className="bg-gray-50 p-3 rounded">
                            <div className="flex justify-between items-end mb-2">
                                <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Projects (Chapters)</h4>
                                <button
                                    onClick={() => handleOpenCreateProject(lecture)}
                                    className="text-xs bg-indigo-600 text-white px-2 py-1 rounded hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
                                    disabled={!series.folder || !lecture.folder}
                                    title={(!series.folder || !lecture.folder) ? "Configure folders to enable creation" : "Create new project from lecture folder"}
                                >
                                    + New Project
                                </button>
                            </div>

                            <div className="space-y-2">
                                {lecture.project_ids.map((pid, pIndex) => {
                                    const proj = allProjects.find(p => p.id === pid);
                                    return (
                                        <div key={pid} className="flex justify-between items-center bg-white border p-2 rounded text-sm group">
                                            {proj ? (
                                                <Link href={`/admin/notes-to-sermon/project/${proj.id}`} className="text-blue-600 hover:underline">
                                                    {proj.title}
                                                </Link>
                                            ) : (
                                                <span className="text-gray-400">Unknown Project ({pid})</span>
                                            )}
                                            <div className="flex items-center space-x-2">
                                                <div className="flex flex-col space-y-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                                                    {pIndex > 0 && (
                                                        <button
                                                            onClick={() => moveProject(lecture.id, lecture.project_ids, pIndex, 'up')}
                                                            className="text-gray-400 hover:text-gray-700 text-[10px] leading-none"
                                                            title="Move Up"
                                                        >
                                                            ▲
                                                        </button>
                                                    )}
                                                    {pIndex < lecture.project_ids.length - 1 && (
                                                        <button
                                                            onClick={() => moveProject(lecture.id, lecture.project_ids, pIndex, 'down')}
                                                            className="text-gray-400 hover:text-gray-700 text-[10px] leading-none"
                                                            title="Move Down"
                                                        >
                                                            ▼
                                                        </button>
                                                    )}
                                                </div>
                                                <button
                                                    onClick={() => handleRemoveProject(lecture.id, pid)}
                                                    className="text-gray-400 hover:text-red-600 opacity-0 group-hover:opacity-100 transition-opacity ml-2"
                                                    title="Remove from lecture"
                                                >
                                                    &times;
                                                </button>
                                            </div>
                                        </div>
                                    );
                                })}
                                {lecture.project_ids.length === 0 && (
                                    <div className="text-xs text-gray-400 italic">No projects assigned</div>
                                )}
                            </div>

                            {/* Assign UI */}
                            {assignTargetLectureId === lecture.id ? (
                                <div className="mt-3 flex space-x-2">
                                    <select
                                        className="border rounded px-2 py-1 text-sm flex-1"
                                        value={selectedProjectId}
                                        onChange={e => setSelectedProjectId(e.target.value)}
                                    >
                                        <option value="">Select a project...</option>
                                        {allProjects
                                            .filter(p => !p.series_id && !p.lecture_id) // Show only unassigned projects? Or allow steal? Let's show currently unassigned.
                                            .map(p => (
                                                <option key={p.id} value={p.id}>{p.title}</option>
                                            ))
                                        }
                                        {/* Also show projects from other series? Maybe too complex for now. Just unassigned. */}
                                    </select>
                                    <button
                                        onClick={handleAssignProject}
                                        className="bg-green-600 text-white px-2 py-1 rounded text-xs disabled:opacity-50"
                                        disabled={!selectedProjectId}
                                    >
                                        Add
                                    </button>
                                    <button
                                        onClick={() => setAssignTargetLectureId(null)}
                                        className="text-gray-500 px-2 py-1 text-xs"
                                    >
                                        Cancel
                                    </button>
                                </div>
                            ) : (
                                <button
                                    onClick={() => setAssignTargetLectureId(lecture.id)}
                                    className="mt-3 text-xs text-blue-600 hover:text-blue-800 flex items-center"
                                >
                                    + Add existing project
                                </button>
                            )}
                        </div>
                    </div>
                ))}
            </div>
            {/* Create Project Modal */}
            {creatingProjectForLecture && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                    <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
                        <div className="p-4 border-b flex justify-between items-center bg-gray-50 rounded-t-lg">
                            <h3 className="font-bold text-lg">Create Project for "{creatingProjectForLecture.title}"</h3>
                            <button onClick={() => setCreatingProjectForLecture(null)} className="text-gray-500 hover:text-gray-700">&times;</button>
                        </div>

                        <div className="p-4 flex-1 overflow-y-auto">
                            <div className="mb-4">
                                <label className="block text-sm font-medium mb-1">Project Title</label>
                                <input
                                    className="border rounded w-full p-2"
                                    value={newProjectTitle}
                                    onChange={e => setNewProjectTitle(e.target.value)}
                                />
                            </div>

                            <label className="block text-sm font-medium mb-2">Select Images from {series?.folder}/{creatingProjectForLecture.folder}</label>

                            {isFetchingImages ? (
                                <div className="py-8 text-center text-gray-500">Loading images...</div>
                            ) : projectImages.length === 0 ? (
                                <div className="py-8 text-center text-gray-500 bg-gray-50 rounded">No images found in this folder.</div>
                            ) : (
                                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                    {projectImages.map((img: any) => (
                                        <div
                                            key={img.filename}
                                            className={`
                                                relative border rounded p-2 cursor-pointer transition-all
                                                ${selectedImages.includes(img.filename) ? 'ring-2 ring-indigo-500 bg-indigo-50 border-indigo-500' : 'hover:bg-gray-50'}
                                            `}
                                            onClick={() => toggleImageSelection(img.filename)}
                                        >
                                            <div className="aspect-[3/4] bg-gray-200 mb-2 rounded overflow-hidden">
                                                {/* Use /image endpoint - but handle encoded filename */}
                                                <img
                                                    src={`/api/admin/notes-to-sermon/image/${encodeURIComponent(img.filename)}`}
                                                    alt={img.filename}
                                                    className="w-full h-full object-cover"
                                                    loading="lazy"
                                                />
                                            </div>
                                            <div className="text-xs truncate font-mono" title={img.filename}>
                                                {/* Display only basename for cleanliness if path matches folder */}
                                                {img.filename.split('/').pop()}
                                            </div>
                                            {selectedImages.includes(img.filename) && (
                                                <div className="absolute top-2 right-2 bg-indigo-600 text-white rounded-full p-1 shadow">
                                                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M5 13l4 4L19 7"></path></svg>
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>

                        <div className="p-4 border-t bg-gray-50 rounded-b-lg flex justify-between items-center">
                            <span className="text-sm text-gray-600">{selectedImages.length} images selected</span>
                            <div className="space-x-3">
                                <button onClick={() => setCreatingProjectForLecture(null)} className="px-4 py-2 text-gray-600 hover:text-gray-800">Cancel</button>
                                <button
                                    onClick={handleCreateProject}
                                    className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
                                    disabled={selectedImages.length === 0}
                                >
                                    Create Project
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

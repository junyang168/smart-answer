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
    project_ids: string[];
}

interface LectureSeries {
    id: string;
    title: string;
    description?: string;
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
    const [lectureTitle, setLectureTitle] = useState("");
    const [lectureDesc, setLectureDesc] = useState("");

    // Assign Project UI State
    const [assignTargetLectureId, setAssignTargetLectureId] = useState<string | null>(null);
    const [selectedProjectId, setSelectedProjectId] = useState("");

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
                setSeries(await serRes.json());
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

    const handleCreateLecture = async () => {
        if (!lectureTitle) return alert("Title required");
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/series/${seriesId}/lectures`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: lectureTitle, description: lectureDesc }),
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
                    </div>
                    {/* Series Actions (Edit/Delete) could go here */}
                </div>
            </div>

            <hr className="my-6" />

            {/* Lectures Section */}
            <div className="flex justify-between items-center mb-4">
                <h2 className="text-xl font-bold">Lectures</h2>
                <button
                    onClick={() => setIsCreatingLecture(true)}
                    className="bg-blue-600 text-white px-3 py-1.5 rounded hover:bg-blue-700 text-sm"
                >
                    + Add Lecture
                </button>
            </div>

            {isCreatingLecture && (
                <div className="bg-gray-50 p-4 rounded border mb-6">
                    <h3 className="font-semibold mb-2">New Lecture</h3>
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
                    <div className="flex justify-end space-x-2">
                        <button onClick={() => setIsCreatingLecture(false)} className="px-3 py-1 text-gray-600">Cancel</button>
                        <button onClick={handleCreateLecture} className="px-3 py-1 bg-blue-600 text-white rounded">Create</button>
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
                                <p className="text-sm text-gray-500">{lecture.description}</p>
                            </div>
                            <div className="flex space-x-2">
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
                            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Projects (Chapters)</h4>

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
        </div>
    );
}

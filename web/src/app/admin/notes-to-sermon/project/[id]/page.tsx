"use client";

import React from "react";
import MultiPageEditor from "./MultiPageEditor";

export default function Page({ params }: { params: { id: string } }) {
    const projectId = params.id;
    const [projectTitle, setProjectTitle] = React.useState<string>(projectId);

    React.useEffect(() => {
        fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}`)
            .then(res => res.json())
            .then(data => setProjectTitle(data.title))
            .catch(() => setProjectTitle(projectId)); // Fallback
    }, [projectId]);

    return (
        <div className="h-[calc(100vh-64px)] flex flex-col">
            <div className="flex-1 overflow-hidden relative">
                <MultiPageEditor projectId={projectId} />
            </div>
        </div>
    );
}

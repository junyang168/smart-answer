# Functional Specification: Notes to Sermon Transformation System

## 1. Introduction
The "Notes to Sermon" system is a specialized AI-powered workflow designed to transform raw, handwritten, or structured lecture notes into fully developed spoken manuscripts (sermons). It leverages a team of specialized AI agents to ensure theological accuracy, exegetical depth, and rhetorical flow.

## 2. User Personas
*   **The Preacher/Pastor**: The primary user. They provide raw notes and expect a draft that sounds like them—conversational, passionate, and doctrinally sound—without needing to write every word from scratch.
*   **The Editor**: A staff member who reviews the AI generation, manages project metadata, and refines the final text.

## 3. Core Features

### 3.1. Project & Series Management
*   **Series Hierarchy**: Projects (individual sermons/chapters) are organized into "Lectures" and "Series".
*   **Contextual Awareness**: The system understands the broader theme of the Series and the specific focus of the current Lecture when generating content.

### 3.2. Multi-Agent Generation Workflow
The generation process is not a "black box" but a visible collaboration between distinct AI personas:
1.  **Exegetical Scholar**:
    *   **Input**: Raw verses and notes.
    *   **Action**: Conducts deep philological research (Greek/Hebrew word studies).
    *   **Output**: Detailed "Exegetical Notes" artifact.
2.  **Theologian**:
    *   **Input**: Source notes + Exegetical findings.
    *   **Action**: Checks for doctrinal consistency and aligns with the Series theme.
    *   **Output**: "Theological Analysis" artifact.
3.  **Illustrator**:
    *   **Input**: Core message and theological points.
    *   **Action**: Brainstorms 3-5 vivid, modern metaphors or stories.
    *   **Output**: "Illustration Ideas" artifact.
4.  **Architect (Structuring Specialist)**:
    *   **Input**: Enriched notes.
    *   **Action**: Intelligently splits the content into "Macro-Beats" (logical sections).
    *   **Output**: Visualized "Beat Cards" showing the sermon's structure.
5.  **Homiletician (Drafter)**:
    *   **Input**: All previous research + specific beat.
    *   **Action**: Writes the *spoken* manuscript for one beat at a time.
    *   **Goal**: "Speak to the people," avoiding bullet points or academic summary.
6.  **Critic**:
    *   **Input**: Drafted beat.
    *   **Action**: Reviews against strict criteria (No "In summary", no bullet points).
    *   **outcome**: PASS (proceed) or FAIL (rewrite request).

### 3.3. Live Generation Dashboard
A real-time interface (`/generation`) allowing users to:
*   **Monitor Progress**: See which agent is currently active via status indicators.
*   **View Artifacts**: Click on agent icons (when green) to read their specific outputs in a rich Markdown modal.
*   **Inspect Logs**: Watch a live stream of agent "thoughts" and system actions.

### 3.4. Output Visualization
*   **Rich Markdown**: All agent outputs differ from standard text; they support:
    *   **Scripture Tooltips**: Hovering over references (e.g., `John 3:16`) shows the full text.
    *   **Collapsible Alerts**: Beats and notes are wrapped in collapsible sections (`> [!NOTE]`) for better readability.

### 3.5. Reliability Features
*   **Resumability**: If the process stops (e.g., browser close), it resumes from the last completed agent step.
*   **Restart Capability**: A "Restart" button allows users to wipe progress and re-run the workflow from scratch (useful for testing different prompts).

## 4. User Stories
*   *As a Pastor, I want to see the "Exegetical Notes" before the draft is written, so I can trust the biblical foundation.*
*   *As an Editor, I want to restart the generation if the "Architect" splits the beats incorrectly, so I can get a better structure.*
*   *As a User, I want to see a progress bar for each beat being drafted, so I know how long the remaining process will take.*

## 5. Non-Functional Requirements
*   **Latency**: The full workflow can take 2-5 minutes depending on note length; the UI must handle this without timing out (using async polling).
*   **Persistence**: All state is saved to disk (`json` files), ensuring no work is lost on server restart.


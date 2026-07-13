# 達拉斯聖道教會 AI 輔助查經平臺 (Smart Answer)

這是 **達拉斯聖道教會** 的網站，也是教會 AI 輔助查經的平臺。 這個新平臺由教會同工藉著 AI 技術，把王守仁教授多年講道的語音內容整理，並轉化為文字，成為門徒靈修、查經的寶貴資源。

## 主要功能 (Key Features)

在新網站上，你可以：

1.  **講道影音庫**: 在線上收聽收看王守仁教授講道部分或全部講道的錄音與錄像。
2.  **AI 輔助查經講稿**: 閱讀周五團契查經的講稿—這些內容源於弟兄姊妹的討論，經 AI 輔助潤色後更為完整， 并獲取最新的團契信息。
3.  **信仰問答集**: 查詢團契中真實的信仰問答，AI 整理並潤色成貼近生活的答問集。
4.  **主題文章**: 閱讀由教授多篇講道整合而成的主題文章。
5.  **教會資訊管理**: 查詢教會團契及主日崇拜信息。

## Architecture (Technical Specs)

The application follows a microservices-like architecture with a Next.js frontend and a Python backend service.

### Components

1.  **Frontend (Web UI)**:
    -   **Path**: `web/`
    -   **Tech**: Next.js, Tailwind CSS
    -   **Port**: 3003
    -   **Description**: Provides the user interface for sermon archives, Bible study notes, and church management.

2.  **Admin & Orchestration Service (Backend API)**:
    -   **Path**: `backend/`
    -   **Tech**: FastAPI, Python
    -   **Port**: 8222 in local development, 8555 in production service scripts
    -   **Description**: The core application backend for the **new Smart Answer UI**. Handles:
        -   Church management (fellowships, sermons, Sunday services).
        -   Webcast and slide management.
        -   API endpoints under `/sc_api` and `/admin`.
        -   Browser-facing new-app calls should use Next.js API routes such as `/api/sc_api/...` and `/api/admin/...`; direct `/sc_api/...` may be routed by nginx to the legacy backend in production.

3.  **Legacy Web Application**:
    -   **Path**: `/web` (URL path), served from static files. Source code not included.
    -   **Backend**: Port **8008** (via Nginx reverse proxy). Source code not included.
    -   **Description**: An older web interface co-hosted on the same infrastructure.

### Infrastructure

-   **Nginx**: Acts as a reverse proxy, 
        For legacy app, routing traffic to the appropriate services based on URL paths (`/web`, `/sc_api`, etc.).
        For new app, routing internet traffic to the appropriate services (`/`, ).
-   **Data Stores**:
    -   **FileSystem**: Used for storing slides, audio, and generated markdown files.
    -   Fellowship documents live under `DATA_BASE_DIR/fellowship/docs/YYYY-MM-DD` unless `FELLOWSHIP_DOCS_DIR` is set. Public fellowship Markdown pages are rendered server-side by Next.js and require the Next process to read that same directory.
    -   Fellowship public input files are the prepared manuscript Markdown, PPTX, and local MP4 recording copy. Generated analysis/transcript files, extracted audio, cache files, and Google Meet chat files are hidden from public document lists.
    -   Fellowship `sourceLinks` are for teaching/source material links only. Do not store the shared Google Meet Recordings folder there; recordings should be copied into the dated fellowship docs folder when they are meant to be public inputs.

## Getting Started

### Prerequisites

-   Node.js and npm
-   Python 3.x
-   Nginx (optional, for production-like routing)
-   Google Gemini API Key (and other LLM keys as needed)

### Installation

1.  **Clone the repository**:
    ```bash
    git clone <repository-url>
    cd smart-answer
    ```

2.  **Backend Setup**:
    Navigate to the `backend` directory, create a virtual environment, and install dependencies:
    ```bash
    cd backend
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cd ..
    ```

3.  **Frontend Setup**:
    Navigate to the `web` directory and install dependencies:
    ```bash
    cd web
    npm install
    ```

### Running the Application

1.  **Admin & Orchestration Service (Backend)**:
    ```bash
    source backend/.venv/bin/activate
    uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8222
    ```
    Fellowship recording transcription requires an `ffmpeg` executable. The backend resolves it in this order:
    `FFMPEG_PATH`, system `ffmpeg`, then the packaged `imageio-ffmpeg` dependency from `backend/requirements.txt`.
    When a Google Drive recording is used for analysis, the backend may download a local MP4 copy into the dated fellowship docs folder so the public site can serve it like the PPTX and manuscript.

2.  **Web UI**:
    ```bash
    cd web
    npm run dev
    ```
    Access at `http://localhost:3003`.

### Nginx Configuration

See `docs/infrastructure.md` for the Nginx configuration used to route requests to these services.

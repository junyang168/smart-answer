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
    -   **Port**: 8222 (mapped to `/sc_api` and `/api/admin` via Next.js proxy)
    -   **Description**: The core application backend for the **new Smart Answer UI**. Handles:
        -   Church management (fellowships, sermons, Sunday services).
        -   Webcast and slide management.
        -   API endpoints under `/sc_api` and `/admin`.

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

2.  **Web UI**:
    ```bash
    cd web
    npm run dev
    ```
    Access at `http://localhost:3003`.

### Nginx Configuration

See `docs/infrastructure.md` for the Nginx configuration used to route requests to these services.

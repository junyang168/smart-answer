# Development Infrastructure

## Nginx Proxy Configuration
The development environment uses an Nginx proxy (installed via Homebrew) listening on port **8888**. It routes traffic to various local services.

### Routing Topology

| Path | Target Service | Port | Description |
|------|----------------|------|-------------|
| `/web/` | Static Files | N/A | **Legacy Web App** (Served from `/opt/homebrew/var/www/church`) |
| `/sc_api/` | Backend Service | 8008 | **Legacy Web App** Semantic Search / Backend API |
| `/public` | Backend Service | 8008 | **Legacy Web App** Public assets/endpoints |
| `/static` | Backend Service | 8008 | **Legacy Web App** Static assets |
| `/` | Web UI (Next.js) | 3003 | Frontend Application |

### Smart Answer API Routing Notes

The deployed site has two different API surfaces that look similar but are routed differently:

- `/sc_api/...` is owned by nginx and currently targets the legacy backend on port `8008`.
- `/api/sc_api/...` is owned by the Next.js app and is proxied by `web/src/app/api/sc_api/[[...path]]/route.ts` to the current FastAPI backend (`SC_API_SERVICE_URL` or `FULL_ARTICLE_SERVICE_URL`).

For new Smart Answer frontend code, prefer `/api/sc_api/...` for browser-facing JSON API calls. Do not link public fellowship documents directly to `/sc_api/...`; in production that path can be intercepted by nginx and served by the wrong backend.

Public fellowship attachment downloads use the Next.js local file route:

- `/api/fellowship-documents/[date]/[documentPath]`

This route reads from the fellowship docs directory and implements byte-range responses for MP4 files. Large fellowship recordings should not be downloaded through `/api/sc_api/.../documents/...`, because that path adds an extra backend/proxy hop and has caused stalled downloads for large recordings.

Fellowship Markdown pages under `/resources/fellowship/[date]/docs/[...documentPath]` are server-rendered by Next.js. The Next process reads public `.md` files directly from:

- `FELLOWSHIP_DOCS_DIR`, when set; otherwise
- `DATA_BASE_DIR/fellowship/docs`

The same fellowship docs directory must therefore be readable by both the FastAPI backend and the Next.js frontend process in production.

### Nginx Configuration Block
```nginx
server {
    listen       8888 default_server;
    server_name  _;

    location /web/ {
        root   /opt/homebrew/var/www/church;
        index  index.html index.htm;
    }
    
    location /sc_api/ {
        proxy_pass http://127.0.0.1:8008;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10M;
        client_body_buffer_size 128k;        
        proxy_read_timeout 180s;        
    }

    location /public {
        proxy_pass http://127.0.0.1:8008;
        # ... headers ...
    }
     
    location /static {
        proxy_pass http://127.0.0.1:8008;
        # ... headers ...
    }

    # Smart Answer - UI
    location / {
        proxy_pass http://localhost:3003;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        # ... headers ...
    }
}
```

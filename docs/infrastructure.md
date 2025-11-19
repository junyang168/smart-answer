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

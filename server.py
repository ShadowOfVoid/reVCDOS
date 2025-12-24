import os
import argparse
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
import httpx
import additions.saves as saves
from additions.auth import BasicAuthMiddleware

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--custom_saves", action="store_true")
parser.add_argument("--login", type=str)
parser.add_argument("--password", type=str)
parser.add_argument("--vcsky_local", action="store_true", help="Serve vcsky from local directory instead of proxy")
parser.add_argument("--vcbr_local", action="store_true", help="Serve vcbr from local directory instead of proxy")
parser.add_argument("--vcsky_url", type=str, default="https://cdn.dos.zone/vcsky/", help="Custom vcsky proxy URL")
parser.add_argument("--vcbr_url", type=str, default="https://br.cdn.dos.zone/vcsky/", help="Custom vcbr proxy URL")
args = parser.parse_args()

app = FastAPI()

if args.login and args.password:
    app.add_middleware(BasicAuthMiddleware, username=args.login, password=args.password)

if args.custom_saves:
    app.include_router(saves.router)

VCSKY_BASE_URL = args.vcsky_url
VCBR_BASE_URL = args.vcbr_url

def request_to_url(request: Request, path: str, base_url: str):
    query_string = str(request.url.query) if request.url.query else ""
    url = f"{base_url}{path}"
    if query_string:
        url = f"{url}?{query_string}"
    return url

async def _proxy_request(request: Request, url: str):
    client = httpx.AsyncClient(timeout=None)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}
    
    req = client.build_request(request.method, url, headers=headers)
    r = await client.send(req, stream=True)
    
    excluded_headers = {"content-length", "transfer-encoding", "connection", "keep-alive", "upgrade", "content-encoding", "x-content-encoding"}
    response_headers = {k: v for k, v in r.headers.items() if k.lower() not in excluded_headers}
    
    response_headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response_headers["Cross-Origin-Embedder-Policy"] = "require-corp"

    return StreamingResponse(
        r.aiter_bytes(),
        status_code=r.status_code,
        headers=response_headers,
        background=BackgroundTask(client.aclose)
    )

# vcsky routes - either local or proxy
if args.vcsky_local:
    app.mount("/vcsky", StaticFiles(directory="vcsky"), name="vcsky")
else:
    @app.api_route("/vcsky/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    async def vc_sky_proxy(request: Request, path: str):
        return await _proxy_request(request, request_to_url(request, path, VCSKY_BASE_URL))

# vcbr routes - either local or proxy
if args.vcbr_local:
    @app.get("/vcbr/{file_path:path}")
    async def serve_vcbr_local(file_path: str):
        file_location = os.path.join("vcbr", file_path)
        if not os.path.isfile(file_location):
            raise HTTPException(status_code=404, detail="File not found")
        
        headers = {
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp"
        }
        
        media_type = "application/octet-stream"
        if file_path.endswith(".wasm.br"):
            media_type = "application/wasm"
            headers["Content-Encoding"] = "br"
        elif file_path.endswith(".data.br"):
            media_type = "application/octet-stream"
            headers["Content-Encoding"] = "br"
        elif file_path.endswith(".wasm"):
            media_type = "application/wasm"
        
        return FileResponse(file_location, media_type=media_type, headers=headers)
else:
    @app.api_route("/vcbr/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    async def vc_br_proxy(request: Request, path: str):
        return await _proxy_request(request, request_to_url(request, path, VCBR_BASE_URL))

@app.get("/")
async def read_index():
    if os.path.exists("dist/index.html"):
        with open("dist/index.html", "r", encoding="utf-8") as f:
            content = f.read()
        
        # Inject custom_saves status
        custom_saves_val = "1" if args.custom_saves else "0"
        content = content.replace(
            'new URLSearchParams(window.location.search).get("custom_saves") === "1"',
            f'"{custom_saves_val}" === "1"'
        )
        
        return Response(content, media_type="text/html", headers={
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp"
        })
    return Response("index.html not found", status_code=404)

app.mount("/", StaticFiles(directory="dist"), name="root")

if __name__ == "__main__":
    import uvicorn
    print(f"Starting server on http://localhost:{args.port}")
    print(f"vcsky: {'local' if args.vcsky_local else 'proxy'} ({VCSKY_BASE_URL if not args.vcsky_local else 'vcsky/'})")
    print(f"vcbr: {'local' if args.vcbr_local else 'proxy'} ({VCBR_BASE_URL if not args.vcbr_local else 'vcbr/'})")
    uvicorn.run(app, host="0.0.0.0", port=args.port)

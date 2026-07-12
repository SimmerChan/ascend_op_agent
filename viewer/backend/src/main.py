"""Viewer backend main entry"""

import sys
from pathlib import Path

# Add backend src and project root to path
backend_src = Path(__file__).parent.absolute()
project_root = backend_src.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(backend_src))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.checkpoints import router as checkpoints_router
from routes.sessions import router as sessions_router

app = FastAPI(
    title="Agent Conversation Visualizer API",
    description="API for visualizing agent-user interaction flows",
    version="0.1.0",
)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(sessions_router)
app.include_router(checkpoints_router)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3001)
from dotenv import load_dotenv

load_dotenv()

import os
import uvicorn
from app.main import app

import logging

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Starting server...")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=os.getenv("PORT", 8765),
        reload=os.getenv("DEBUG", False),
    )
    logger.info("Server stopped.")

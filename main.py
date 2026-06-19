from app.core.settings import settings

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    import uvicorn

    logger.info("Starting server...")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.debug,
    )
    logger.info("Server stopped.")

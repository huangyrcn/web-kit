"""FastAPI app: wires routers, error handling and background tasks."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__, browser, settings
from .download import router as download_router
from .errors import WebkitError, classify, webkit_error_handler
from .page import router as page_router
from .search import router as search_router
from .status import egress_loop, router as status_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("webkit")


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await browser.ensure_browser()
    except Exception as e:  # noqa: BLE001 - Chrome may still be starting
        logger.warning("initial CDP connect failed (retried on demand): %s", e)
    probe = asyncio.create_task(egress_loop())
    yield
    probe.cancel()
    await browser.shutdown()


app = FastAPI(title="web-kit", version=__version__, lifespan=lifespan)
app.add_exception_handler(WebkitError, webkit_error_handler)


@app.exception_handler(RequestValidationError)
async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
    return JSONResponse({"error": "invalid_request", "message": f"{where}: {first.get('msg', 'invalid')}"},
                        status_code=400)


@app.exception_handler(Exception)
async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    err = classify(exc)
    return JSONResponse(err.to_dict(), status_code=err.status)


for r in (search_router, page_router, download_router, status_router):
    app.include_router(r)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_level="info", proxy_headers=False)


if __name__ == "__main__":
    run()

from fastapi import APIRouter

from app.insights.api.factcheck_routes import router as factcheck_router
from app.insights.api.insight_routes import router as insight_router

# Combined router exposed to main.py. Both sub-routers keep their own
# prefix (`/insights`, `/fact-check`) so URLs stay stable: with the `/v1`
# parent prefix from main.py, the final paths are
# `/v1/insights/...` and `/v1/fact-check`.
router = APIRouter()
router.include_router(insight_router)
router.include_router(factcheck_router)

__all__ = ["router", "insight_router", "factcheck_router"]

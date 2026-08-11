"""Single aggregation point for all workbench routers."""

from fastapi import APIRouter

from lucking.api.routes import (
    auth,
    broker_recommendations,
    calendar,
    j_gold,
    stocks,
    task_status,
    watchlists,
)

api_router = APIRouter()
for router in (
    auth.router,
    calendar.router,
    stocks.router,
    watchlists.router,
    broker_recommendations.router,
    j_gold.router,
    task_status.router,
):
    api_router.include_router(router)

__all__ = ["api_router"]

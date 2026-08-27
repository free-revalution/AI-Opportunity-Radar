"""Async order repository — DB access for v2.0 commercial orders."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, Opportunity


class OrderRepository:
    """Thin async wrapper over the `orders` table.

    Used by the /orders dashboard and the Content Center's
    "Mark Sold" flow. Keeps SQL in one place so tests can stub it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------- queries -------------------------
    async def list_paginated(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        channel: Optional[str] = None,
        delivery_status: Optional[str] = None,
        opportunity_id: Optional[int] = None,
    ) -> tuple[Sequence[Order], int]:
        """Return (rows, total_count).

        Newest first. All filters are ANDed.
        """
        base = select(Order)
        count_q = select(func.count()).select_from(Order)

        if channel:
            base = base.where(Order.channel == channel)
            count_q = count_q.where(Order.channel == channel)
        if delivery_status:
            base = base.where(Order.delivery_status == delivery_status)
            count_q = count_q.where(Order.delivery_status == delivery_status)
        if opportunity_id is not None:
            base = base.where(Order.opportunity_id == opportunity_id)
            count_q = count_q.where(Order.opportunity_id == opportunity_id)

        base = base.order_by(Order.created_at.desc(), Order.id.desc())
        base = base.limit(limit).offset(offset)

        result = await self.session.execute(base)
        total = await self.session.scalar(count_q) or 0
        return result.scalars().all(), int(total)

    async def get_by_id(self, order_id: int) -> Optional[Order]:
        return await self.session.get(Order, order_id)

    async def stats(self) -> dict[str, Any]:
        """Aggregate stats for the /orders dashboard.

        Returns a dict with:
            * total_orders       — int
            * total_revenue_cny  — float (sum of amount_cny across all rows)
            * delivered_count    — int (delivery_status='delivered')
            * confirmed_count    — int (delivery_status='confirmed')
            * by_channel         — [{channel, count, revenue_cny}, …] sorted desc
            * by_delivery_status — {status: count}
        """
        total_orders = await self.session.scalar(select(func.count()).select_from(Order)) or 0

        # SUM over Numeric returns Decimal; coerce to float for JSON.
        total_revenue = await self.session.scalar(select(func.coalesce(func.sum(Order.amount_cny), 0))) or 0

        # By channel ----------------------------------------------------------
        by_channel_q = (
            select(
                Order.channel.label("channel"),
                func.count().label("count"),
                func.coalesce(func.sum(Order.amount_cny), 0).label("revenue"),
            )
            .group_by(Order.channel)
            .order_by(func.count().desc())
        )
        by_channel_rows = (await self.session.execute(by_channel_q)).all()
        by_channel = [
            {"channel": r.channel, "count": int(r.count), "revenue_cny": float(r.revenue)}
            for r in by_channel_rows
        ]

        # By delivery status --------------------------------------------------
        by_status_q = (
            select(Order.delivery_status, func.count()).group_by(Order.delivery_status)
        )
        by_delivery_status = {
            row[0]: int(row[1]) for row in (await self.session.execute(by_status_q)).all()
        }

        return {
            "total_orders": int(total_orders),
            "total_revenue_cny": float(total_revenue),
            "delivered_count": int(by_delivery_status.get("delivered", 0)),
            "confirmed_count": int(by_delivery_status.get("confirmed", 0)),
            "pending_count": int(by_delivery_status.get("pending", 0)),
            "by_channel": by_channel,
            "by_delivery_status": by_delivery_status,
        }

    # ------------------------- commands -------------------------
    async def create(self, **fields: Any) -> Order:
        order = Order(**fields)
        self.session.add(order)
        await self.session.flush()
        # After flush, server-side defaults (`created_at`, `updated_at`) and
        # any computed columns need an explicit refresh — otherwise reading
        # them later in the request handler can trigger a lazy-load that
        # fails with `MissingGreenlet` because the async DB driver can't be
        # awaited outside a greenlet context.
        await self.session.refresh(order)
        return order

    async def update_status(self, order: Order, new_status: str) -> Order:
        order.delivery_status = new_status
        await self.session.flush()
        # Same reason as `create` — the `onupdate=func.now()` trigger on
        # `updated_at` is server-side; after the UPDATE the in-memory
        # column is marked stale and the next attribute access would
        # trigger a lazy refresh. Force it now while we're still in the
        # request's async context.
        await self.session.refresh(order)
        return order

    async def link_opportunity(self, order: Order, opportunity: Opportunity) -> None:
        """Sanity helper — sets opp.content_status='sold' in one shot."""
        opportunity.content_status = "sold"
        await self.session.flush()

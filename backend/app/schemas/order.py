"""Pydantic schemas for the v2.0 commercial-order endpoints.

These mirror `app/models/orders.py` but use Pydantic-friendly types
(string IDs for opportunity/order, snake_case fields, numeric fields
parsed as `Decimal` then coerced to float in the response so JSON
output stays numeric).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ChannelName = Literal[
    "xianyu",
    "xiaohongshu",
    "wechat",
    "wechat_article",
    "feishu",
    "direct",
    "other",
]

DeliveryStatus = Literal[
    "pending",
    "delivered",
    "confirmed",
    "refunded",
    "cancelled",
]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class OrderCreateRequest(BaseModel):
    """Payload for `POST /api/internal/orders`.

    All three fields the spec calls out are present (`customer_*`,
    `amount_cny` / payment metadata, `delivery_status`). Plus an
    `opportunity_id` to anchor the sale to a content asset.
    """

    opportunity_id: int = Field(..., ge=1, description="Anchor opportunity")
    customer_name: str = Field(..., min_length=1, max_length=128)
    customer_contact: Optional[str] = Field(None, max_length=255)
    amount_cny: Decimal = Field(..., ge=0, description="Sale price in CNY")
    channel: ChannelName
    payment_method: Optional[str] = Field(None, max_length=64)
    payment_reference: Optional[str] = Field(None, max_length=255)
    delivery_status: DeliveryStatus = "pending"
    notes: Optional[str] = Field(None, max_length=2000)

    # When true, also flip the anchor opportunity's content_status to
    # 'sold'. Default true — every new order from the Content Center
    # "Mark Sold" button should count as a conversion.
    mark_opportunity_sold: bool = True


class OrderStatusUpdateRequest(BaseModel):
    delivery_status: DeliveryStatus


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class OrderResponse(BaseModel):
    """Public view of an Order."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    opportunity_id: int
    opportunity_title: Optional[str] = None  # joined for convenience

    customer_name: str
    customer_contact: Optional[str] = None

    amount_cny: float
    channel: str
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None

    delivery_status: str
    commercial_status_snapshot: Optional[str] = None
    notes: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    total: int
    limit: int
    offset: int
    generated_at: datetime


class OrderStatsResponse(BaseModel):
    """Aggregated sales stats for the /orders dashboard."""

    total_orders: int
    total_revenue_cny: float
    delivered_count: int
    confirmed_count: int
    pending_count: int
    by_channel: list[dict[str, Any]]
    by_delivery_status: dict[str, int]


class MarkSoldWithOrderResponse(BaseModel):
    """Returned by `POST /content/{id}/mark_sold` when an order is attached."""

    opportunity_id: int
    content_status: str
    commercial_status: str
    order: OrderResponse

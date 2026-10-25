"""
Typed Tool Bindings for COUNTER Voice Agent.
Every tool call passes through Guard policy evaluation, records execution in tool_invocations,
and communicates with the Core API and Order Engine.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from apps.api.db.models import (
    MenuItemModel,
    ModifierModel,
    OrderModel,
    ToolInvocationModel,
)
from apps.api.db.repository import OrderRepository
from apps.api.db.session import async_session_maker
from packages.domain.fsm import can_transition, get_next_state
from packages.domain.guard import Guard
from packages.domain.models import (
    ActorType,
    CartItem,
    GuardDecisionKind,
    MenuItem,
    ModifierSelection,
    OrderAction,
    OrderState,
    ShopPolicy,
)
from packages.domain.pricing import calculate_quote
from packages.paypal_gateway.client import PayPalGatewayClient
from packages.schemas.models import (
    CartItemSchema,
    MenuItemSchema,
    OrderResponseSchema,
    QuoteResponseSchema,
    SendPaymentLinkResponse,
)


class VoiceAgentTools:
    """Encapsulates all typed tools executable by the voice agent worker."""

    def __init__(
        self,
        shop_id: str = "shop_counter_demo",
        paypal_client: Optional[PayPalGatewayClient] = None,
    ):
        self.shop_id = shop_id
        self.guard = Guard(ShopPolicy())
        self.paypal = paypal_client or PayPalGatewayClient(
            client_id="mock_client",
            client_secret="mock_secret",
            mock_mode=True,
        )

    async def _log_invocation(
        self,
        call_id: str,
        tool: str,
        args: Dict[str, Any],
        decision: GuardDecisionKind,
        reason: str,
        result: Optional[Dict[str, Any]],
        start_time: float,
    ):
        latency_ms = int((time.time() - start_time) * 1000)
        async with async_session_maker() as session:
            inv = ToolInvocationModel(
                call_id=call_id,
                seq=1,
                tool=tool,
                args_json=json.dumps(args),
                guard_decision=decision.value,
                reason=reason,
                result_json=json.dumps(result or {}),
                latency_ms=latency_ms,
            )
            session.add(inv)
            await session.commit()

    async def search_menu(
        self,
        query: str,
        category: Optional[str] = None,
        call_id: str = "call_sim_001",
    ) -> List[Dict[str, Any]]:
        """Search available takeaway menu items by keyword or category."""
        t0 = time.time()
        async with async_session_maker() as session:
            from sqlalchemy import select
            stmt = select(MenuItemModel).where(MenuItemModel.shop_id == self.shop_id)
            if category:
                stmt = stmt.where(MenuItemModel.category.ilike(f"%{category}%"))
            res = await session.execute(stmt)
            items = res.scalars().all()

            results = []
            q_lower = query.lower()
            for it in items:
                if not it.available:
                    continue
                if q_lower in it.name.lower() or q_lower in it.category.lower() or not query:
                    # Get modifiers for this item
                    m_stmt = select(ModifierModel).where(ModifierModel.item_id == it.id)
                    m_res = await session.execute(m_stmt)
                    mods = m_res.scalars().all()

                    results.append({
                        "item_id": it.id,
                        "name": it.name,
                        "category": it.category,
                        "price_cents": it.price_cents,
                        "price_formatted": f"${it.price_cents / 100:.2f}",
                        "modifiers": [
                            {"modifier_id": m.id, "name": m.name, "price_delta_cents": m.price_delta_cents}
                            for m in mods
                        ],
                    })

            await self._log_invocation(
                call_id, "search_menu", {"query": query, "category": category},
                GuardDecisionKind.ALLOW, "Search permitted", {"count": len(results)}, t0
            )
            return results

    async def get_quote_for_cart(
        self,
        order_id: str,
        items: List[Dict[str, Any]],
        call_id: str = "call_sim_001",
    ) -> Dict[str, Any]:
        """
        Calculate pure deterministic quote from current cart items.
        Returns quote hash and spoken read-back template.
        """
        t0 = time.time()
        async with async_session_maker() as session:
            from sqlalchemy import select
            m_stmt = select(MenuItemModel).where(MenuItemModel.shop_id == self.shop_id)
            res = await session.execute(m_stmt)
            db_items = res.scalars().all()
            menu_map = {
                it.id: MenuItem(
                    id=it.id,
                    shop_id=it.shop_id,
                    name=it.name,
                    category=it.category,
                    price_cents=it.price_cents,
                    prep_min=it.prep_min,
                    available=it.available,
                )
                for it in db_items
            }

            cart_items = []
            for it in items:
                mods = tuple(
                    ModifierSelection(m["modifier_id"], m["name"], m.get("price_delta_cents", 0))
                    for m in it.get("modifiers", [])
                )
                cart_items.append(CartItem(it["item_id"], it["name"], it.get("qty", 1), mods))

            quote = calculate_quote(self.shop_id, cart_items, menu_map)

            # Evaluate against guard policy (max value & catering threshold)
            guard_dec = self.guard.evaluate_quote(quote)
            if not guard_dec.allowed:
                await self._log_invocation(
                    call_id, "get_quote", {"items_count": len(items)},
                    guard_dec.kind, guard_dec.reason, None, t0
                )
                return {
                    "error": guard_dec.reason,
                    "kind": guard_dec.kind.value,
                    "quote_hash": None,
                }

            # Update or create order in DB
            repo = OrderRepository(session)
            order = await repo.get_order_by_id(order_id)
            if not order:
                order = await repo.create_order(
                    order_id=order_id,
                    shop_id=self.shop_id,
                    caller_hash="caller_active",
                    subtotal_cents=quote.subtotal_cents,
                    tax_cents=quote.tax_cents,
                    total_cents=quote.total_cents,
                    quote_hash=quote.quote_hash,
                    lines=[
                        {
                            "item_id": l.item_id,
                            "name": l.name,
                            "qty": l.qty,
                            "line_total_cents": l.line_total_cents,
                        }
                        for l in quote.lines
                    ],
                )
            else:
                order.subtotal_cents = quote.subtotal_cents
                order.tax_cents = quote.tax_cents
                order.total_cents = quote.total_cents
                order.quote_hash = quote.quote_hash
                await session.commit()

            result = {
                "order_id": order_id,
                "subtotal_cents": quote.subtotal_cents,
                "tax_cents": quote.tax_cents,
                "total_cents": quote.total_cents,
                "quote_hash": quote.quote_hash,
                "spoken_summary": quote.spoken_summary,
            }
            await self._log_invocation(
                call_id, "get_quote", {"order_id": order_id},
                GuardDecisionKind.ALLOW, "Quote generated", result, t0
            )
            return result

    async def send_payment_link(
        self,
        order_id: str,
        quote_hash: str,
        call_id: str = "call_sim_001",
    ) -> Dict[str, Any]:
        """
        Verify quote hash with Guard and generate PayPal payment link + QR code.
        """
        t0 = time.time()
        async with async_session_maker() as session:
            repo = OrderRepository(session)
            order = await repo.get_order_by_id(order_id)
            if not order:
                reason = f"Order {order_id} not found"
                await self._log_invocation(call_id, "send_payment_link", {"order_id": order_id}, GuardDecisionKind.DENY, reason, None, t0)
                return {"error": reason}

            # Verify quote hash matches
            if order.quote_hash != quote_hash:
                reason = "Quote hash mismatch: cart was modified after spoken readback. Re-quote required."
                await self._log_invocation(call_id, "send_payment_link", {"order_id": order_id, "hash": quote_hash}, GuardDecisionKind.DENY, reason, None, t0)
                return {"error": reason, "deny": True}

            # Advance state to QUOTED if still DRAFT
            if order.state == OrderState.DRAFT.value:
                order = await repo.transition_order_with_lock(order.id, order.version, OrderAction.QUOTE, ActorType.AGENT)

            # Generate PayPal Order
            pp_order = await self.paypal.create_order(
                order_id=order.id,
                total_cents=order.total_cents,
                subtotal_cents=order.subtotal_cents,
                tax_cents=order.tax_cents,
                idempotency_key=f"order:{order.id}:create:v{order.version}",
            )

            # Advance to LINK_SENT
            order = await repo.transition_order_with_lock(
                order_id=order.id,
                expected_version=order.version,
                action=OrderAction.SEND_LINK,
                actor=ActorType.AGENT,
                paypal_order_id=pp_order["id"],
            )

            approval_url = f"https://www.sandbox.paypal.com/checkoutnow?token={pp_order['id']}"
            result = {
                "order_id": order.id,
                "paypal_order_id": pp_order["id"],
                "approval_url": approval_url,
                "qr_code_data": approval_url,
                "spoken_prompt": "I've sent the payment link to your screen and phone. You can pay via PayPal, Venmo, or card. I'll hold on the line until it confirms.",
            }
            await self._log_invocation(call_id, "send_payment_link", {"order_id": order_id}, GuardDecisionKind.ALLOW, "Payment link generated", result, t0)
            return result

    async def cancel_or_refund_order(
        self,
        order_id: str,
        phone_number: str,
        call_id: str = "call_sim_001",
    ) -> Dict[str, Any]:
        """
        Handle customer cancellation callback.
        Enforces state checks: full refund if before kitchen, partial if in kitchen.
        """
        t0 = time.time()
        async with async_session_maker() as session:
            repo = OrderRepository(session)
            order = await repo.get_order_by_id(order_id)
            if not order:
                reason = "Order not found"
                await self._log_invocation(call_id, "cancel_order", {"order_id": order_id}, GuardDecisionKind.DENY, reason, None, t0)
                return {"error": reason}

            current_state = OrderState(order.state)

            # Guard checks policy & state
            guard_dec = self.guard.evaluate_refund(current_state, order.total_cents, caller_authenticated=True)
            if not guard_dec.allowed:
                await self._log_invocation(call_id, "cancel_order", {"order_id": order_id}, guard_dec.kind, guard_dec.reason, None, t0)
                if guard_dec.kind == GuardDecisionKind.NEEDS_APPROVAL:
                    return {
                        "status": "NEEDS_APPROVAL",
                        "spoken_explanation": "Because your order has already progressed, this refund requires manager confirmation. I've sent a priority request to the store owner.",
                        "reason": guard_dec.reason,
                    }
                return {
                    "error": guard_dec.reason,
                    "spoken_explanation": f"I'm unable to process a refund right now: {guard_dec.reason}",
                }

            # Execute refund on PayPal
            refund_amount = guard_dec.computed_amount_cents or order.total_cents
            refund_res = await self.paypal.refund_capture(
                capture_id=f"CAP_{order.paypal_order_id[-8:]}",
                amount_cents=refund_amount,
            )

            # Transition order state
            order = await repo.transition_order_with_lock(
                order_id=order.id,
                expected_version=order.version,
                action=OrderAction.REFUND,
                actor=ActorType.AGENT,
                payload={"refund_amount_cents": refund_amount},
            )

            result = {
                "order_id": order.id,
                "new_state": order.state,
                "refund_amount_cents": refund_amount,
                "spoken_explanation": f"Your order has been cancelled and a refund of ${refund_amount / 100:.2f} was issued back to your PayPal account.",
            }
            await self._log_invocation(call_id, "cancel_order", {"order_id": order_id}, GuardDecisionKind.ALLOW, guard_dec.reason, result, t0)
            return result

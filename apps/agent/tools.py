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
    VaultMandate,
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
        session: Optional[Any] = None,
    ):
        latency_ms = int((time.time() - start_time) * 1000)
        import uuid
        inv = ToolInvocationModel(
            id=str(uuid.uuid4()),
            call_id=call_id,
            seq=1,
            tool=tool,
            args_json=json.dumps(args),
            guard_decision=decision.value,
            reason=reason,
            result_json=json.dumps(result or {}),
            latency_ms=latency_ms,
        )
        if session is not None:
            session.add(inv)
            await session.commit()
        else:
            async with async_session_maker() as s:
                s.add(inv)
                await s.commit()

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
                GuardDecisionKind.ALLOW, "Search permitted", {"count": len(results)}, t0,
                session=session,
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
                    guard_dec.kind, guard_dec.reason, None, t0, session=session,
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
                GuardDecisionKind.ALLOW, "Quote generated", result, t0, session=session,
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
                await self._log_invocation(call_id, "send_payment_link", {"order_id": order_id}, GuardDecisionKind.DENY, reason, None, t0, session=session)
                return {"error": reason}

            # Verify quote hash matches
            if order.quote_hash != quote_hash:
                reason = "Quote hash mismatch: cart was modified after spoken readback. Re-quote required."
                await self._log_invocation(call_id, "send_payment_link", {"order_id": order_id, "hash": quote_hash}, GuardDecisionKind.DENY, reason, None, t0, session=session)
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
            await self._log_invocation(call_id, "send_payment_link", {"order_id": order_id}, GuardDecisionKind.ALLOW, "Payment link generated", result, t0, session=session)
            return result

    async def check_saved_payment(
        self,
        caller_phone: str,
        call_id: str = "call_sim_001",
    ) -> Dict[str, Any]:
        """Check if caller has an active PayPal vault mandate for voice-confirmed checkout."""
        t0 = time.time()
        async with async_session_maker() as session:
            repo = OrderRepository(session)
            mandate = await repo.get_active_mandate(customer_hash=caller_phone, shop_id=self.shop_id)
            if not mandate:
                await self._log_invocation(call_id, "check_saved_payment", {"phone": caller_phone}, GuardDecisionKind.ALLOW, "No saved payment", {"has_saved": False}, t0, session=session)
                return {"has_saved": False}

            result = {
                "has_saved": True,
                "mandate_id": mandate.id,
                "max_charge_cents": mandate.max_charge_cents,
                "max_formatted": f"${mandate.max_charge_cents / 100:.2f}",
            }
            await self._log_invocation(call_id, "check_saved_payment", {"phone": caller_phone}, GuardDecisionKind.ALLOW, "Active mandate found", result, t0, session=session)
            return result

    async def charge_saved_payment(
        self,
        order_id: str,
        quote_hash: str,
        caller_phone: str,
        spoken_consent_recorded: bool = True,
        call_id: str = "call_sim_001",
    ) -> Dict[str, Any]:
        """
        Execute voice-confirmed charge on caller's saved PayPal wallet.
        Enforces Guard checks: active mandate, spending ceiling, quote hash match, and recorded consent.
        """
        t0 = time.time()
        async with async_session_maker() as session:
            repo = OrderRepository(session)
            order = await repo.get_order_by_id(order_id)
            if not order:
                reason = f"Order {order_id} not found"
                await self._log_invocation(call_id, "charge_saved_payment", {"order_id": order_id}, GuardDecisionKind.DENY, reason, None, t0, session=session)
                return {"error": reason, "fallback_to_link": True}

            mandate_model = await repo.get_active_mandate(customer_hash=caller_phone, shop_id=self.shop_id)
            domain_mandate = None
            if mandate_model:
                domain_mandate = VaultMandate(
                    id=mandate_model.id,
                    vault_id=mandate_model.vault_id,
                    customer_hash=mandate_model.customer_hash,
                    shop_id=mandate_model.shop_id,
                    max_charge_cents=mandate_model.max_charge_cents,
                    status=mandate_model.status,
                )

            # Build dummy quote object for Guard evaluation
            from packages.domain.models import QuoteLine
            dummy_quote = calculate_quote(
                shop_id=self.shop_id,
                cart=[],
                menu={},
            )
            # Reconstruct quote from order figures
            quote_for_guard = type(dummy_quote)(
                shop_id=self.shop_id,
                lines=(),
                subtotal_cents=order.subtotal_cents,
                tax_rate_bps=825,
                tax_cents=order.tax_cents,
                total_cents=order.total_cents,
                quote_hash=order.quote_hash or quote_hash,
                spoken_summary="",
            )

            current_state = OrderState(order.state)
            if current_state == OrderState.DRAFT:
                order = await repo.transition_order_with_lock(order.id, order.version, OrderAction.QUOTE, ActorType.AGENT)
                current_state = OrderState.QUOTED

            guard_dec = self.guard.evaluate_charge_saved_payment(
                current_state=current_state,
                provided_quote_hash=quote_hash,
                current_quote=quote_for_guard,
                mandate=domain_mandate,
                spoken_consent_recorded=spoken_consent_recorded,
            )

            if not guard_dec.allowed:
                await self._log_invocation(call_id, "charge_saved_payment", {"order_id": order_id}, guard_dec.kind, guard_dec.reason, None, t0, session=session)
                return {
                    "error": guard_dec.reason,
                    "fallback_to_link": True,
                    "spoken_explanation": f"I couldn't charge your saved PayPal automatically: {guard_dec.reason}. Let me send a payment link to your phone instead.",
                }

            # Execute PayPal charge using Vault ID
            assert domain_mandate is not None
            charge_res = await self.paypal.charge_vault_id(
                vault_id=domain_mandate.vault_id,
                order_id=order.id,
                total_cents=order.total_cents,
                subtotal_cents=order.subtotal_cents,
                tax_cents=order.tax_cents,
                idempotency_key=f"order:{order.id}:vault_charge:v{order.version}",
            )

            # Transition order state to CAPTURED
            order = await repo.transition_order_with_lock(
                order_id=order.id,
                expected_version=order.version,
                action=OrderAction.CHARGE_SAVED_PAYMENT,
                actor=ActorType.AGENT,
                payload={"vault_id": domain_mandate.vault_id, "mandate_id": domain_mandate.id},
                paypal_order_id=charge_res["id"],
            )

            # Record in payments ledger
            await repo.record_payment(
                order_id=order.id,
                paypal_order_id=charge_res["id"],
                capture_id=f"CAP_VAULT_{charge_res['id'][-8:]}",
                amount_cents=order.total_cents,
                fee_cents=(order.total_cents * 29 // 1000) + 30,
            )

            result = {
                "order_id": order.id,
                "paypal_order_id": charge_res["id"],
                "status": "CAPTURED",
                "amount_cents": order.total_cents,
                "spoken_confirmation": f"Got it, charged ${order.total_cents / 100:.2f} to your saved PayPal! The kitchen is preparing your order now, ready for pickup in 15 minutes.",
            }
            await self._log_invocation(call_id, "charge_saved_payment", {"order_id": order_id}, GuardDecisionKind.ALLOW, guard_dec.reason, result, t0, session=session)
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
                await self._log_invocation(call_id, "cancel_order", {"order_id": order_id}, GuardDecisionKind.DENY, reason, None, t0, session=session)
                return {"error": reason}

            current_state = OrderState(order.state)

            # Guard checks policy & state
            guard_dec = self.guard.evaluate_refund(current_state, order.total_cents, caller_authenticated=True)
            if not guard_dec.allowed:
                await self._log_invocation(call_id, "cancel_order", {"order_id": order_id}, guard_dec.kind, guard_dec.reason, None, t0, session=session)
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
            await self._log_invocation(call_id, "cancel_order", {"order_id": order_id}, GuardDecisionKind.ALLOW, guard_dec.reason, result, t0, session=session)
            return result

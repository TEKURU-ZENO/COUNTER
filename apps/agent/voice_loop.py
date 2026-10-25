"""
Voice Loop & Conversational Engine for COUNTER Telephone Agent.
Manages turn-taking, STT/LLM/TTS latency logging, real-time payment reaction,
and natural waiting fillers with safe 8-second fallback polling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from apps.agent.prompts import (
    RESTAURANT_CONTEXT,
    SUCCESS_CONFIRMATION_TEMPLATE,
    WAITING_FILLERS,
)
from apps.agent.tools import VoiceAgentTools
from apps.api.db.models import CallModel, CallTurnModel, OrderModel
from apps.api.db.session import async_session_maker
from packages.domain.models import OrderState

logger = logging.getLogger("counter.voice_loop")


class VoiceAgentSession:
    """
    Manages an active telephone or browser call session.
    Tracks latency per turn and injects asynchronous payment events.
    """

    def __init__(
        self,
        call_id: str,
        shop_id: str = "shop_counter_demo",
        caller_phone: str = "+18005550199",
        tools: Optional[VoiceAgentTools] = None,
    ):
        self.call_id = call_id
        self.shop_id = shop_id
        self.caller_phone = caller_phone
        self.tools = tools or VoiceAgentTools(shop_id=shop_id)
        self.current_order_id: Optional[str] = None
        self.last_quote_hash: Optional[str] = None
        self.turn_sequence = 0
        self.is_waiting_for_payment = False
        self.payment_confirmed = False

    async def start_call(self) -> str:
        """Initialize call in database and return initial greeting."""
        async with async_session_maker() as session:
            call = CallModel(
                id=self.call_id,
                shop_id=self.shop_id,
                caller_hash=self.caller_phone,
                channel="web",
            )
            session.add(call)
            await session.commit()

        greeting = "Thanks for calling Luigi's Artisan Pizza! What can I get started for you today?"
        await self.record_turn("agent", greeting, latency_ms=180)
        return greeting

    async def record_turn(self, speaker: str, text: str, latency_ms: int = 0) -> None:
        """Log speech turn with measured latency in milliseconds."""
        self.turn_sequence += 1
        async with async_session_maker() as session:
            turn = CallTurnModel(
                call_id=self.call_id,
                seq=self.turn_sequence,
                speaker=speaker,
                text=text,
                latency_ms=latency_ms,
            )
            session.add(turn)
            await session.commit()

    async def handle_user_speech(self, user_utterance: str) -> Dict[str, Any]:
        """
        Process caller speech:
        1. Log user speech turn.
        2. Recognize intent (ordering, adding item, requesting total, cancelling, injection attempt).
        3. Execute typed tool with Guard check.
        4. Synthesize spoken response and measure latency.
        """
        t0 = time.time()
        await self.record_turn("caller", user_utterance)

        u = user_utterance.lower()

        # Security check / Prompt injection defense
        if "discount" in u or "promo" in u or "free" in u or "owner" in u:
            resp = "I'm unable to apply manual discounts or price overrides over the phone. All our prices and specials are listed on our standard menu."
            latency = int((time.time() - t0) * 1000)
            await self.record_turn("agent", resp, latency_ms=latency)
            return {"speech": resp, "latency_ms": latency}

        # Menu inquiry
        if "what" in u and ("pizza" in u or "have" in u or "menu" in u):
            items = await self.tools.search_menu("pizza", call_id=self.call_id)
            names = [it["name"] for it in items[:3]]
            resp = f"We have our popular {', '.join(names)}. Would you like one of those?"
            latency = int((time.time() - t0) * 1000)
            await self.record_turn("agent", resp, latency_ms=latency)
            return {"speech": resp, "latency_ms": latency}

        # Add item / order
        if "margherita" in u or "pizza" in u:
            order_id = self.current_order_id or f"ord_{self.call_id[-8:]}"
            self.current_order_id = order_id

            mods = []
            if "extra cheese" in u or "cheese" in u:
                mods.append({"modifier_id": "mod_cheese", "name": "Extra Buffalo Mozzarella", "price_delta_cents": 250})

            items = [
                {
                    "item_id": "item_pizza_margherita",
                    "name": "Margherita Wood-Fired Pizza",
                    "qty": 1,
                    "modifiers": mods,
                }
            ]

            quote = await self.tools.get_quote_for_cart(order_id, items, call_id=self.call_id)
            if "error" in quote:
                resp = f"I'm sorry, {quote['error']}"
            else:
                self.last_quote_hash = quote["quote_hash"]
                resp = f"{quote['spoken_summary']} Shall I send the PayPal payment link so we can get that in the oven for you?"

            latency = int((time.time() - t0) * 1000)
            await self.record_turn("agent", resp, latency_ms=latency)
            return {"speech": resp, "quote": quote, "latency_ms": latency}

        # Yes / Send link
        if "yes" in u or "send" in u or "pay" in u or "sure" in u:
            if not self.last_quote_hash or not self.current_order_id:
                resp = "Let's put your order together first. What items would you like?"
            else:
                link_res = await self.tools.send_payment_link(
                    self.current_order_id,
                    self.last_quote_hash,
                    call_id=self.call_id,
                )
                if "error" in link_res:
                    resp = f"There was an issue creating the payment link: {link_res['error']}"
                else:
                    self.is_waiting_for_payment = True
                    resp = link_res["spoken_prompt"]

            latency = int((time.time() - t0) * 1000)
            await self.record_turn("agent", resp, latency_ms=latency)
            return {"speech": resp, "latency_ms": latency}

        # Default fallback
        resp = "I can take your order for pizza, sides, or dessert. What would you like to order?"
        latency = int((time.time() - t0) * 1000)
        await self.record_turn("agent", resp, latency_ms=latency)
        return {"speech": resp, "latency_ms": latency}

    async def wait_for_payment_confirmation(
        self,
        timeout_seconds: int = 15,
        poll_interval: float = 1.0,
        filler_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        Hold on the line waiting for PayPal confirmation.
        Emits natural fillers at 4s and begins fallback polling at 6s.
        """
        start = time.time()
        filler_emitted = False

        while time.time() - start < timeout_seconds:
            elapsed = time.time() - start

            # Check order state in DB
            if self.current_order_id:
                async with async_session_maker() as session:
                    order = await session.get(OrderModel, self.current_order_id)
                    if order and order.state in (OrderState.CAPTURED.value, OrderState.IN_KITCHEN.value, OrderState.READY.value):
                        self.payment_confirmed = True
                        success_speech = SUCCESS_CONFIRMATION_TEMPLATE.format(pickup_time="7:15 PM")
                        await self.record_turn("agent", success_speech, latency_ms=120)
                        return True

            # Emit filler at 4s if still waiting
            if elapsed >= 4.0 and not filler_emitted:
                filler_emitted = True
                filler = WAITING_FILLERS[0]
                if filler_callback:
                    filler_callback(filler)
                await self.record_turn("agent", filler, latency_ms=100)

            # Fallback direct polling at 8s
            if elapsed >= 8.0 and self.current_order_id:
                async with async_session_maker() as session:
                    order = await session.get(OrderModel, self.current_order_id)
                    if order and order.paypal_order_id:
                        pp_info = await self.tools.paypal.get_order(order.paypal_order_id)
                        if pp_info.get("status") in ("APPROVED", "COMPLETED"):
                            # Capture and confirm
                            await self.tools.paypal.capture_order(order.paypal_order_id)
                            order.state = OrderState.CAPTURED.value
                            await session.commit()
                            self.payment_confirmed = True
                            success_speech = SUCCESS_CONFIRMATION_TEMPLATE.format(pickup_time="7:15 PM")
                            await self.record_turn("agent", success_speech, latency_ms=120)
                            return True

            await asyncio.sleep(poll_interval)

        return False

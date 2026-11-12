"""
Phone channel via Twilio Programmable Voice.

Twilio does speech-to-text (<Gather input="speech">) and text-to-speech (<Say>), and posts
each recognised utterance here. Caller ID comes from Twilio's `From`, which is spoofable: it
only selects the caller's record; saved-wallet charges still require the SMS code (see
docs/threat-model.md). Every request's X-Twilio-Signature is verified.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Optional
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from apps.agent.session import CallSession, LLMUnavailableError, TurnResult
from apps.api.security import get_container
from apps.core.container import Container

router = APIRouter(prefix="/api/v1/twilio")


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    payload = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


async def _verified_form(request: Request, c: Container) -> dict[str, str]:
    if not c.settings.twilio_configured or c.settings.twilio_auth_token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    form = {k: str(v) for k, v in (await request.form()).items()}
    url = c.settings.api_public_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    expected = twilio_signature(c.settings.twilio_auth_token.get_secret_value(), url, form)
    if not hmac.compare_digest(expected, request.headers.get("x-twilio-signature", "")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bad Twilio signature")
    return form


def _twiml(c: Container, call_id: str, speech: str, *, ended: bool, handoff: Optional[str]) -> Response:
    say = f"<Say>{escape(speech)}</Say>"
    if handoff and c.settings.human_handoff_number:
        body = f"{say}<Dial>{escape(c.settings.human_handoff_number)}</Dial>"
    elif ended or handoff:
        body = f"{say}<Hangup/>"
    else:
        action = quoteattr(f"/api/v1/twilio/turn?call_id={call_id}")
        silence = escape(f"/api/v1/twilio/silence?call_id={call_id}")
        # <Say> nested in <Gather> lets the caller barge in over the agent.
        body = (f'<Gather input="speech" action={action} method="POST" speechTimeout="auto" timeout="6" '
                f'language="en-US">{say}</Gather><Redirect method="POST">{silence}</Redirect>')
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


def _from_result(c: Container, call_id: str, result: TurnResult) -> Response:
    return _twiml(c, call_id, result.speech, ended=result.ended, handoff=result.handoff_reason)


@router.post("/voice")
async def incoming_call(request: Request, c: Container = Depends(get_container)) -> Response:
    form = await _verified_form(request, c)
    caller = form.get("From", "")
    if not caller or c.llm is None:
        return _twiml(c, "", "Sorry, we can't take phone orders right now.", ended=True, handoff=None)
    session, greeting = await CallSession.start(c, caller_phone=caller, channel="phone")
    return _twiml(c, session.call_id, greeting, ended=False, handoff=None)


@router.post("/turn")
async def turn(request: Request, call_id: str, c: Container = Depends(get_container)) -> Response:
    form = await _verified_form(request, c)
    session = await CallSession.resume(c, call_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    speech = form.get("SpeechResult", "").strip()
    if not speech:
        return _from_result(c, call_id, await session.handle_silence())
    try:
        result = await session.handle_turn(speech, caller_phone=form.get("From"))
    except LLMUnavailableError as exc:
        return _twiml(c, call_id, str(exc), ended=True, handoff=None)
    return _from_result(c, call_id, result)


@router.post("/silence")
async def silence(request: Request, call_id: str, c: Container = Depends(get_container)) -> Response:
    await _verified_form(request, c)
    session = await CallSession.resume(c, call_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return _from_result(c, call_id, await session.handle_silence())

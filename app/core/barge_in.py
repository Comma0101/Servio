from __future__ import annotations
import asyncio
import logging
from enum import Enum
from typing import Protocol

logger = logging.getLogger(__name__)

class BargeInState(Enum):
    IDLE = 0      # no TTS in flight
    TTS_PLAY = 1  # we are streaming audio
    WAIT_MARK = 2 # audio done, waiting for telephony ack

class TelephonyAdapter(Protocol):
    async def clear_down(self) -> None:
        """Instructs the telephony provider to clear any playing audio."""
        ...

class TTSAdapter(Protocol):
    async def provider_interrupt(self) -> None:
        """Instructs the TTS provider to stop generating/sending audio and clean up resources for the current utterance."""
        ...
    # Optionally:
    # async def ensure_open(self) -> bool: ...
    # async def send_tts_request(self, text: str, req_id: str) -> bool: ...
    # async def handle_tts_stream(self, req_id: str, controller: 'BargeInController') -> None: ...


class STTAdapter(Protocol):
    # The main requirement for an STT adapter is to detect speech start
    # and inform the BargeInController.
    # This might be through direct event handling or by wrapping its STT message processing.
    async def process_stt_stream(self, controller: 'BargeInController') -> None:
        """Processes the STT stream and calls controller.speech_start() on speech detection."""
        ...

class BargeInController:
    def __init__(self, tel: TelephonyAdapter, tts: TTSAdapter, call_sid: str | None = "N/A"):
        self.tel = tel
        self.tts = tts
        self.state = BargeInState.IDLE
        self.current_tts_reqid: str | None = None
        self.call_sid = call_sid # For logging
        logger.info(f"BargeInController initialized for call {self.call_sid} with state IDLE.")

    def _log_state_transition(self, new_state: BargeInState, event: str, reqid: str | None = None):
        old_state = self.state
        self.state = new_state
        log_msg = f"BargeInState transition for call {self.call_sid}: {old_state.name} -> {new_state.name} (event: {event}"
        if reqid:
            log_msg += f", reqid: {reqid}"
        log_msg += ")"
        logger.info(log_msg)

    # Inbound from TTS provider (via TTSAdapter or handler)
    async def on_tts_audio_start(self, reqid: str):
        """Called when the first audio packet for a new TTS request is about to be processed/sent."""
        if self.state == BargeInState.IDLE:
            self.current_tts_reqid = reqid
            self._log_state_transition(BargeInState.TTS_PLAY, "tts_audio_start", reqid)
        elif self.state in (BargeInState.TTS_PLAY, BargeInState.WAIT_MARK):
            # This implies a new TTS request is starting while a previous one was active or waiting for mark.
            # This should ideally be preceded by a speech_start() or explicit interruption.
            # If speech_start() was called, state would be IDLE.
            # This path indicates a potential logic issue or a very rapid succession of TTS without user speech.
            logger.warning(f"Call {self.call_sid}: on_tts_audio_start called for reqid {reqid} while state is {self.state.name} (current_reqid: {self.current_tts_reqid}). Forcing interruption of old, then starting new.")
            await self.tts.provider_interrupt() # Interrupt previous TTS provider state
            self.current_tts_reqid = reqid
            self._log_state_transition(BargeInState.TTS_PLAY, "tts_audio_start_after_implicit_interrupt", reqid)


    async def on_tts_audio_end(self, reqid: str):
        """Called when the final audio packet for a TTS request has been processed/sent."""
        if self.state == BargeInState.TTS_PLAY and self.current_tts_reqid == reqid:
            self._log_state_transition(BargeInState.WAIT_MARK, "tts_audio_end", reqid)
        elif self.current_tts_reqid != reqid:
            logger.warning(f"Call {self.call_sid}: on_tts_audio_end for reqid {reqid} does not match current_tts_reqid {self.current_tts_reqid}. State: {self.state.name}. Ignoring.")
        elif self.state != BargeInState.TTS_PLAY:
            logger.warning(f"Call {self.call_sid}: on_tts_audio_end for reqid {reqid} called but state is {self.state.name}. Ignoring.")


    # Inbound from telephony adapter (e.g., Twilio mark)
    async def on_telephony_ack(self, reqid: str | None): # reqid from mark name
        """Called when the telephony provider acknowledges that TTS playback has finished (e.g., Twilio mark)."""
        if self.state == BargeInState.WAIT_MARK:
            if self.current_tts_reqid == reqid:
                self._log_state_transition(BargeInState.IDLE, "telephony_ack", reqid)
                self.current_tts_reqid = None
            else:
                logger.warning(f"Call {self.call_sid}: Telephony ACK for reqid {reqid} does not match expected {self.current_tts_reqid} in WAIT_MARK state. Ignoring.")
        elif self.state == BargeInState.IDLE and reqid and self.current_tts_reqid == reqid:
            # This can happen if speech_start caused a transition to IDLE, then a late mark arrives.
            logger.info(f"Call {self.call_sid}: Received telephony ACK for reqid {reqid} while already in IDLE state (likely due to prior barge-in). Current_tts_reqid cleared.")
            self.current_tts_reqid = None
        else:
            logger.info(f"Call {self.call_sid}: Telephony ACK for reqid {reqid} received in unexpected state {self.state.name} or mismatched reqid. Ignoring.")

    # Inbound from STT adapter or handler
    async def speech_detected(self) -> bool:
        """
        Called by the STT component when user speech is detected.
        Returns True if barge-in was triggered, False otherwise.
        """
        if self.state in (BargeInState.TTS_PLAY, BargeInState.WAIT_MARK):
            logger.info(f"Call {self.call_sid}: Speech detected during {self.state.name} (reqid: {self.current_tts_reqid}). Triggering barge-in.")
            
            # Order of operations:
            # 1. Interrupt TTS provider (stop sending new audio, cancel tasks)
            # 2. Clear telephony playback buffer
            # 3. Update state
            
            active_reqid_before_interrupt = self.current_tts_reqid
            
            # Plan's order: Telephony clear first, then TTS provider interrupt.
            await self.tel.clear_down()         # Adapter handles telephony clear
            await self.tts.provider_interrupt() # Adapter handles provider-specific TTS stop

            self._log_state_transition(BargeInState.IDLE, "speech_detected_barge_in", active_reqid_before_interrupt)
            self.current_tts_reqid = None # Ensure current_tts_reqid is cleared after barge-in
            return True
        else: # BargeInState.IDLE
            logger.info(f"Call {self.call_sid}: Speech detected while in IDLE state. No barge-in needed.")
            return False
            
    def get_current_state(self) -> BargeInState:
        return self.state

    def get_current_tts_reqid(self) -> str | None:
        return self.current_tts_reqid

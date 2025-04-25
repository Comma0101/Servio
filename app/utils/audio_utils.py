# /home/comma/Documents/Servio/app/utils/audio_utils.py
import audioop
import base64
import logging

logger = logging.getLogger(__name__)

def pcm_to_ulaw(pcm_data: bytes, sample_width: int = 2) -> bytes:
    """Convert linear PCM audio data to µ-law format."""
    try:
        # Ensure input is 16-bit PCM (sample_width=2) if needed, adjust if TTS gives 8-bit
        ulaw_data = audioop.lin2ulaw(pcm_data, sample_width)
        return ulaw_data
    except audioop.error as e:
        logger.error(f"Audioop error during PCM to µ-law conversion: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during PCM to µ-law conversion: {e}")
        raise

def bytes_to_base64(data: bytes) -> str:
    """Encode bytes to a base64 string."""
    return base64.b64encode(data).decode('utf-8')

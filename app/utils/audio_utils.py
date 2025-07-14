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

def ulaw_to_pcm(ulaw_data: bytes, sample_width: int = 2) -> bytes:
    """Convert µ-law audio data to linear PCM format.
    
    Args:
        ulaw_data: Audio data in µ-law format (8-bit)
        sample_width: Sample width for output PCM (2=16 bit, 1=8 bit)
        
    Returns:
        PCM audio data
    """
    try:
        pcm_data = audioop.ulaw2lin(ulaw_data, sample_width)
        return pcm_data
    except audioop.error as e:
        logger.error(f"Audioop error during µ-law to PCM conversion: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during µ-law to PCM conversion: {e}")
        raise

def bytes_to_base64(data: bytes) -> str:
    """Encode bytes to a base64 string."""
    return base64.b64encode(data).decode('utf-8')

# 24 kHz ➜ 8 kHz μ-law helper
from pydub import AudioSegment, effects

def narrowband(payload_24k_pcm: bytes) -> bytes:
    """
    Converts 24kHz 16-bit linear PCM audio to 8kHz µ-law.
    """
    try:
        seg = AudioSegment(
            payload_24k_pcm,
            sample_width=2,  # 16-bit
            frame_rate=24000,
            channels=1
        )
        # Normalization can be helpful but consider if it's always desired
        # seg = effects.normalize(seg)
        
        # First, resample to 8kHz while keeping it 16-bit PCM
        pcm_8k_16bit_segment = seg.set_frame_rate(8000).set_sample_width(2)
        
        # Then, convert 16-bit linear PCM to µ-law
        ulaw_8k_data = audioop.lin2ulaw(pcm_8k_16bit_segment.raw_data, 2)
        
        return ulaw_8k_data
    except Exception as e:
        logger.error(f"Error in narrowband conversion: {e}", exc_info=True)
        raise

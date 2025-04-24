import logging
import asyncio
import re

class CancelledErrorFilter(logging.Filter):
    """
    Filter out log records for asyncio.CancelledError exceptions
    from any logger in the application.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        """
        Returns False to suppress the log record if it's a CancelledError,
        True otherwise.
        """
        # 1. Check if there's exception info attached to the record
        if record.exc_info:
            exc_type, exc_value, exc_traceback = record.exc_info
            # Check if the exception type is asyncio.CancelledError
            if exc_type is asyncio.CancelledError:
                return False
        
        # 2. Check for CancelledError in formatted messages (when exc_info is not present)
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            # Basic CancelledError check
            if 'asyncio.exceptions.CancelledError' in record.msg:
                return False
            
            # Specific subprocess error patterns
            if any(pattern in record.msg for pattern in [
                "Process SpawnProcess-", 
                "run_until_complete",
                "Traceback (most recent call last):",
                "asyncio/runners.py"
            ]) and 'CancelledError' in record.msg:
                return False
            
            # Handle uvicorn/starlette shutdown messages
            if any(pattern in record.msg for pattern in [
                "starlette/routing.py",
                "uvicorn/lifespan/on.py", 
                "asyncio/queues.py",
                "Process finished with exit code"
            ]):
                return False
        
        # 3. Allow all other log records
        return True

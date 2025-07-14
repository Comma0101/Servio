CREATE TABLE calls (
    id SERIAL PRIMARY KEY,
    call_sid VARCHAR(255) UNIQUE NOT NULL,
    caller_phone VARCHAR(255),
    start_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP WITH TIME ZONE,
    audio_url VARCHAR(255),
    order_id VARCHAR(255),
    metadata JSONB
);

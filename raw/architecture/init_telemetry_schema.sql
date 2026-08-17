-- Inference telemetry and token metrics schema
CREATE TABLE IF NOT EXISTS model_inference_telemetry (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name VARCHAR(128) NOT NULL,
    request_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    prompt_tokens INT NOT NULL,
    completion_tokens INT NOT NULL,
    ttft_ms NUMERIC(10, 2) NOT NULL,
    total_latency_ms NUMERIC(10, 2) NOT NULL,
    tokens_per_sec NUMERIC(10, 2) GENERATED ALWAYS AS (
        CASE WHEN total_latency_ms > 0 THEN (completion_tokens::NUMERIC / (total_latency_ms / 1000.0)) ELSE 0 END
    ) STORED,
    gpu_vram_peak_mb INT NOT NULL,
    status_code INT NOT NULL DEFAULT 200,
    department VARCHAR(64) NOT NULL DEFAULT 'ai_eng'
);

CREATE INDEX IF NOT EXISTS idx_telemetry_model_time ON model_inference_telemetry(model_name, request_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_department ON model_inference_telemetry(department);

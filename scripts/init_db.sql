-- SPECTRAFLOW TimescaleDB initialization script
-- Runs automatically when the TimescaleDB container starts

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Note: The full schema is applied at application startup by
-- spectraflow/telemetry/fingerprinting.py::init_timescale_schema()
-- This file only ensures the extensions are available.

SELECT 'SPECTRAFLOW database initialized' AS status;

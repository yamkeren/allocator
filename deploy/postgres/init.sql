-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Ensure the allocator user has full privileges
GRANT ALL PRIVILEGES ON DATABASE allocator TO allocator;

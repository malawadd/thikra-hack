# Data model

SQLAlchemy 2 and Alembic define Workspace, User, CreativeBrief, Mandate, MandateVersion, ProviderQuote, ProviderDecision, PaymentRecord, PaymentEvent, GenerationRun, Scene, Asset, AssetRelation, Evaluation, EvaluationResult, AuditEvent, RedressCase, CaseNote, and IntegrationHealth.

IDs are UUIDs and timestamps UTC. Money uses integer minor units plus currency. Indexes cover run/payment/case status, timestamps, provider, asset type, and audit run. Mandate versions preserve compilation, edits, confirmation, and amendments. Payment rows contain sanitized references only; one-time credential columns do not exist.

SQLite is the local default. PostgreSQL uses `DATABASE_URL=postgresql+psycopg://...`.
